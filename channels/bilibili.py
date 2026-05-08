import random
import time

import requests

from channels.common import (
    fetch_ready_replies as gateway_fetch_ready_replies,
    finish_reply as gateway_finish_reply,
    gateway_url,
    get_cookie_value,
    load_config,
    load_playwright_cookie_jar,
    submit_events,
)

GATEWAY_URL = None


def make_session(cookie_file):
    session = requests.Session()
    session.cookies = load_playwright_cookie_jar(cookie_file)
    session.headers.update(
        {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9",
            "origin": "https://message.bilibili.com",
            "referer": "https://message.bilibili.com/",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        }
    )
    return session


def check_unread_at(session):
    response = session.get(
        "https://api.vc.bilibili.com/x/im/web/msgfeed/unread",
        params={"build": 0, "mobi_app": "web", "web_location": "333.40164"},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"unread api failed: {data}")
    return int(data.get("data", {}).get("at", 0))


def fetch_at_messages(session):
    response = session.get(
        "https://api.bilibili.com/x/msgfeed/at",
        params={"platform": "web", "build": 0, "mobi_app": "web", "web_location": "333.40164"},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"at api failed: {data}")
    return data.get("data", {}).get("items", [])


def normalize_at_item(item):
    detail = item["item"]
    user = item["user"]
    return {
        "platform": "bilibili",
        "event_id": str(item["id"]),
        "user_id": str(user["mid"]),
        "user_name": user.get("nickname", ""),
        "comment_text": detail.get("source_content", ""),
        "video_url": detail.get("uri", ""),
        "created_at": int(item.get("at_time") or time.time()),
        "raw": {
            "subject_id": detail.get("subject_id"),
            "business_id": detail.get("business_id"),
            "source_id": detail.get("source_id"),
            "root_id": detail.get("root_id") or 0,
            "title": detail.get("title", ""),
            "native_uri": detail.get("native_uri", ""),
        },
    }


def filter_recent_events(events, max_age_seconds):
    if not max_age_seconds:
        return events

    cutoff = int(time.time()) - int(max_age_seconds)
    recent_events = [event for event in events if int(event.get("created_at") or 0) >= cutoff]
    skipped = len(events) - len(recent_events)
    if skipped:
        print(f"skip {skipped} old bilibili events older than {max_age_seconds}s")
    return recent_events


def event_ids(events):
    return {str(event["event_id"]) for event in events}


def only_new_events(events, seen_ids):
    new_events = [event for event in events if str(event["event_id"]) not in seen_ids]
    skipped = len(events) - len(new_events)
    if skipped:
        print(f"skip {skipped} already seen bilibili events")
    seen_ids.update(event_ids(events))
    return new_events


def fetch_ready_replies():
    return gateway_fetch_ready_replies(GATEWAY_URL, "bilibili")


def finish_reply(db_id, ok, error=None):
    return gateway_finish_reply(GATEWAY_URL, db_id, ok, error)


def build_bilibili_reply_payload(reply_job, csrf):
    raw = reply_job["raw"]
    source_id = raw["source_id"]
    root_id = raw.get("root_id") or 0

    return {
        "oid": raw["subject_id"],
        "type": raw["business_id"],
        "message": reply_job["reply_text"],
        "scene": "msg",
        "plat": 1,
        "from": "im-reply",
        "build": 0,
        "mobi_app": "web",
        "root": root_id or source_id,
        "parent": source_id,
        "csrf": csrf,
    }


def send_bilibili_reply(session, reply_job):
    csrf = get_cookie_value(session, "bili_jct")
    if not csrf:
        raise RuntimeError("missing bili_jct cookie")

    response = session.post(
        "https://api.bilibili.com/x/v2/reply/add",
        data=build_bilibili_reply_payload(reply_job, csrf),
        headers={"content-type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(f"reply api failed: {data}")
    return data


def process_ready_replies(session):
    for reply_job in fetch_ready_replies():
        try:
            send_bilibili_reply(session, reply_job)
            finish_reply(reply_job["db_id"], True)
            print(f"replied bilibili event {reply_job['event_id']}")
        except Exception as exc:
            finish_reply(reply_job["db_id"], False, str(exc))
            print(f"failed to reply bilibili event {reply_job['event_id']}: {exc}")


def sleep_seconds(config):
    base = config["unread_poll_base_seconds"]
    jitter = config["unread_poll_jitter_seconds"]
    return max(1, base + random.uniform(-jitter, jitter))


def main():
    global GATEWAY_URL
    config = load_config()
    channel_config = config["channels"]["bilibili"]
    GATEWAY_URL = gateway_url(config)

    session = make_session(channel_config["cookie_file"])
    print("bilibili channel started")

    last_at_fetch = 0
    ignore_existing_on_start = channel_config.get("ignore_existing_on_start", True)
    max_notice_age_seconds = channel_config.get("max_notice_age_seconds")
    seen_event_ids = set()

    if ignore_existing_on_start:
        try:
            baseline_items = fetch_at_messages(session)
            baseline_events = [normalize_at_item(item) for item in baseline_items]
            seen_event_ids.update(event_ids(baseline_events))
            print(
                "baseline bilibili startup: marked "
                f"{len(seen_event_ids)} existing events as read without reply"
            )
        except Exception as exc:
            print(f"baseline bilibili startup failed: {exc}")

    while True:
        try:
            unread_at = check_unread_at(session)
            print(f"unread at: {unread_at}")

            if unread_at > 0 and time.time() - last_at_fetch >= channel_config["at_fetch_min_interval_seconds"]:
                items = fetch_at_messages(session)
                events = [normalize_at_item(item) for item in items]
                events = filter_recent_events(events, max_notice_age_seconds)
                events = only_new_events(events, seen_event_ids)

                if events:
                    result = submit_events(GATEWAY_URL, events)
                    print(f"submitted {len(events)} events: {result}")
                else:
                    print("no bilibili at events to submit")
                last_at_fetch = time.time()

            process_ready_replies(session)

        except Exception as exc:
            print(f"bilibili channel error: {exc}")

        time.sleep(sleep_seconds(channel_config))


if __name__ == "__main__":
    main()
