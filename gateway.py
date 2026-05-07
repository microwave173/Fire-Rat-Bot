from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Full, Queue
import json
import sqlite3
import threading
import time

import bot_core


CONFIG_PATH = Path("config.json")


# Event status meanings:
# new: Stored and queued or waiting to be queued.
# processing: A worker has picked it. Treat this as already read.
# reply_ready: bot_core finished; channel still needs to call the platform reply API.
# replied: The platform reply API succeeded.
# ignored_rate_limited: User exceeded the configured mention limit; read without reply.
# dropped_queue_full: Queue was full; read without reply.
# failed: bot_core or platform reply failed.
STATUS_NEW = "new"
STATUS_PROCESSING = "processing"
STATUS_REPLY_READY = "reply_ready"
STATUS_REPLIED = "replied"
STATUS_IGNORED_RATE_LIMITED = "ignored_rate_limited"
STATUS_DROPPED_QUEUE_FULL = "dropped_queue_full"
STATUS_FAILED = "failed"


def now():
    return int(time.time())


def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


class EventStore:
    def __init__(self, db_path, config):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.lock = threading.Lock()
        self._init_db()

    def connect(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def _init_db(self):
        with self.connect() as conn:
            conn.execute(
                """
                create table if not exists events (
                    id integer primary key autoincrement,
                    platform text not null,
                    event_id text not null,
                    user_id text not null,
                    user_name text,
                    comment_text text not null,
                    video_url text,
                    created_at integer not null,
                    status text not null,
                    reply_text text,
                    raw_json text not null,
                    error text,
                    inserted_at integer not null,
                    updated_at integer not null,
                    unique(platform, event_id)
                )
                """
            )
            conn.execute("create index if not exists idx_events_status on events(status)")
            conn.execute("create index if not exists idx_events_user_time on events(user_id, created_at)")

    def cleanup_old_events(self):
        cutoff = now() - self.config["event_retention_seconds"]
        with self.lock, self.connect() as conn:
            conn.execute("delete from events where inserted_at < ?", (cutoff,))

    def mention_count_in_window(self, user_id):
        cutoff = now() - self.config["rate_limit_window_seconds"]
        with self.lock, self.connect() as conn:
            row = conn.execute(
                """
                select count(*)
                from events
                where user_id = ?
                  and created_at >= ?
                  and status not in (?, ?)
                """,
                (str(user_id), cutoff, STATUS_DROPPED_QUEUE_FULL, STATUS_FAILED),
            ).fetchone()
        return row[0]

    def insert_event(self, event, status):
        ts = now()
        with self.lock, self.connect() as conn:
            try:
                cursor = conn.execute(
                    """
                    insert into events (
                        platform, event_id, user_id, user_name, comment_text,
                        video_url, created_at, status, raw_json, inserted_at, updated_at
                    )
                    values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event["platform"],
                        str(event["event_id"]),
                        str(event["user_id"]),
                        event.get("user_name", ""),
                        event["comment_text"],
                        event.get("video_url", ""),
                        int(event.get("created_at") or ts),
                        status,
                        json.dumps(event.get("raw", {}), ensure_ascii=False),
                        ts,
                        ts,
                    ),
                )
                return cursor.lastrowid, True
            except sqlite3.IntegrityError:
                row = conn.execute(
                    "select id from events where platform = ? and event_id = ?",
                    (event["platform"], str(event["event_id"])),
                ).fetchone()
                return row[0], False

    def update_status(self, event_id, status, reply_text=None, error=None):
        with self.lock, self.connect() as conn:
            conn.execute(
                """
                update events
                set status = ?,
                    reply_text = coalesce(?, reply_text),
                    error = ?,
                    updated_at = ?
                where id = ?
                """,
                (status, reply_text, error, now(), int(event_id)),
            )

    def get_event(self, event_id):
        with self.lock, self.connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("select * from events where id = ?", (int(event_id),)).fetchone()
        return dict(row) if row else None

    def ready_replies(self, platform):
        with self.lock, self.connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                select *
                from events
                where platform = ? and status = ?
                order by id asc
                """,
                (platform, STATUS_REPLY_READY),
            ).fetchall()
        return [dict(row) for row in rows]


class GatewayApp:
    def __init__(self, config):
        self.config = config["gateway"]
        self.store = EventStore(self.config["db_path"], self.config)
        self.queue = Queue(maxsize=self.config["max_queue_size"])
        self.worker_count = self.config["worker_count"]

    def start_workers(self):
        for index in range(self.worker_count):
            thread = threading.Thread(target=self.worker_loop, name=f"worker-{index}", daemon=True)
            thread.start()

    def worker_loop(self):
        while True:
            event_id = self.queue.get()
            try:
                self.process_event(event_id)
            finally:
                self.queue.task_done()

    def process_event(self, event_id):
        self.store.update_status(event_id, STATUS_PROCESSING)
        event = self.store.get_event(event_id)
        if not event:
            return

        try:
            reply_text = bot_core.reply(event["comment_text"], event["video_url"])
            self.store.update_status(event_id, STATUS_REPLY_READY, reply_text=reply_text)
        except Exception as exc:
            self.store.update_status(event_id, STATUS_FAILED, error=str(exc))

    def submit_events(self, events):
        self.store.cleanup_old_events()
        results = []

        for event in events:
            result = self.submit_one_event(event)
            results.append(result)

        return results

    def submit_one_event(self, event):
        mention_count = self.store.mention_count_in_window(event["user_id"])

        if mention_count >= self.config["rate_limit_max_mentions"]:
            event_id, inserted = self.store.insert_event(event, STATUS_IGNORED_RATE_LIMITED)
            return {"event_id": event.get("event_id"), "db_id": event_id, "status": "ignored_rate_limited", "inserted": inserted}

        db_id, inserted = self.store.insert_event(event, STATUS_NEW)
        if not inserted:
            return {"event_id": event.get("event_id"), "db_id": db_id, "status": "duplicate", "inserted": False}

        try:
            self.queue.put_nowait(db_id)
            return {"event_id": event.get("event_id"), "db_id": db_id, "status": "queued", "inserted": True}
        except Full:
            self.store.update_status(db_id, STATUS_DROPPED_QUEUE_FULL)
            return {"event_id": event.get("event_id"), "db_id": db_id, "status": "dropped_queue_full", "inserted": True}

    def ready_replies(self, platform):
        rows = self.store.ready_replies(platform)
        replies = []
        for row in rows:
            replies.append(
                {
                    "db_id": row["id"],
                    "platform": row["platform"],
                    "event_id": row["event_id"],
                    "reply_text": row["reply_text"],
                    "raw": json.loads(row["raw_json"]),
                }
            )
        return replies

    def finish_reply(self, db_id, ok, error=None):
        if ok:
            self.store.update_status(db_id, STATUS_REPLIED)
            return {"db_id": db_id, "status": STATUS_REPLIED}

        self.store.update_status(db_id, STATUS_FAILED, error=error or "reply failed")
        return {"db_id": db_id, "status": STATUS_FAILED}


APP = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")

    def read_json(self):
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        if not body:
            return {}
        return json.loads(body.decode("utf-8"))

    def send_json(self, status, data):
        encoded = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        if self.path == "/health":
            self.send_json(200, {"ok": True, "queue_size": APP.queue.qsize()})
            return

        if self.path.startswith("/replies/ready"):
            platform = "bilibili"
            if "?" in self.path:
                query = self.path.split("?", 1)[1]
                for part in query.split("&"):
                    key, _, value = part.partition("=")
                    if key == "platform" and value:
                        platform = value
            self.send_json(200, {"items": APP.ready_replies(platform)})
            return

        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/events":
            payload = self.read_json()
            events = payload.get("events", [])
            self.send_json(200, {"results": APP.submit_events(events)})
            return

        if self.path == "/replies/finish":
            payload = self.read_json()
            result = APP.finish_reply(payload["db_id"], bool(payload.get("ok")), payload.get("error"))
            self.send_json(200, result)
            return

        self.send_json(404, {"error": "not found"})


def main():
    global APP
    config = load_config()
    APP = GatewayApp(config)
    APP.start_workers()

    host = APP.config["host"]
    port = APP.config["port"]
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"gateway listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
