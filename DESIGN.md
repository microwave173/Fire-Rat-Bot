# Auto Reply Bot Design

This project is an auto-reply bot for comments that mention the account.
The first supported platform is Bilibili, but the structure should leave room
for more platforms later.

## Goals

- Poll platform notifications and find new comments that mention the account.
- Avoid duplicate replies.
- Ignore users who mention the account too frequently.
- Keep the core reply logic platform-independent.
- Keep the first version small and easy to debug.

## Architecture

```text
channels/
  bilibili/
    Poll Bilibili unread count.
    Fetch @ comment list when unread.at > 0.
    Send normalized at_comment_list to gateway.
    Send the final reply back to Bilibili.

gateway/
  Own SQLite state.
  Deduplicate events.
  Apply per-user rate limit.
  Manage a bounded worker queue.
  Call bot_core.

bot_core/
  Read comment context.
  Generate reply text.
  For now, always return "copy that".
```

The gateway should not know too much about Bilibili-specific APIs. Channels
translate platform data into a small common event format before sending it to
the gateway.

## Event Flow

1. Bilibili channel polls unread information.
2. If `unread.data.at > 0`, the channel fetches the full @ message list.
3. The channel converts Bilibili items into normalized comment events.
4. The channel posts those events to the gateway.
5. The gateway deduplicates, rate-limits, and queues acceptable events.
6. A worker calls `bot_core.reply(...)`.
7. The gateway gives the reply result back to the channel.
8. The channel calls the platform reply API.
9. The gateway records the final status.

## Normalized Comment Event

Recommended shape:

```json
{
  "platform": "bilibili",
  "event_id": "1063838426472452",
  "user_id": "39255732",
  "user_name": "64级火球鼠",
  "comment_text": "@293级火球鼠",
  "video_url": "https://www.bilibili.com/video/BV1q4411d7RX",
  "created_at": 1778153815,
  "raw": {}
}
```

`raw` can keep platform-specific data needed by the channel to reply, such as
Bilibili `subject_id`, `business_id`, `source_id`, and `root_id`.

## Bot Core Input

Keep the bot core small and platform-neutral:

```json
{
  "comment_text": "@293级火球鼠",
  "video_url": "https://www.bilibili.com/video/BV1q4411d7RX"
}
```

For the first version, `bot_core` returns:

```json
{
  "reply_text": "copy that"
}
```

## Gateway Status

These statuses are stored in SQLite. Code comments should keep the same meaning.

- `new`: The event is stored and waiting for processing.
- `processing`: A worker has picked the event and is calling `bot_core`.
  This is treated as already read so it will not be processed again.
- `replied`: The platform reply API succeeded.
- `ignored_rate_limited`: The user exceeded the configured mention limit.
  This is intentionally read without reply.
- `dropped_queue_full`: The gateway queue was full. The event is stored but
  not processed.
- `failed`: `bot_core` or the platform reply step failed.

## Rate Limit

The first version uses a simple per-user limit:

```text
max 3 mentions per user per 1 hour
```

Both the count and time window should live in `config.json`.

## Queue Policy

The gateway uses a bounded queue.

If the queue is full, new acceptable events are marked as `dropped_queue_full`.
Already queued tasks are not removed.

This keeps behavior simple and predictable. The expected traffic is low, so
there is no need for complex backpressure in the first version.

## Database Retention

SQLite should not grow forever. A cleanup task can delete old events after a
configured retention window.

Recommended first value:

```text
event_retention_seconds = 86400
```

That keeps one day of events for deduplication and debugging.

## Minimal Config

Recommended `config.json`:

```json
{
  "gateway": {
    "host": "127.0.0.1",
    "port": 8765,
    "db_path": "data/at_bot.sqlite3",
    "worker_count": 3,
    "max_queue_size": 100,
    "event_retention_seconds": 86400,
    "rate_limit_window_seconds": 3600,
    "rate_limit_max_mentions": 3
  },
  "channels": {
    "bilibili": {
      "enabled": true,
      "cookie_file": "bilibili/bilibili_cookies.json",
      "unread_poll_base_seconds": 60,
      "unread_poll_jitter_seconds": 10,
      "at_fetch_min_interval_seconds": 10
    }
  }
}
```

## Bilibili Notes

Use the unread endpoint as a lightweight probe:

```text
GET https://api.vc.bilibili.com/x/im/web/msgfeed/unread
```

Only fetch the heavier @ list when `data.at > 0`:

```text
GET https://api.bilibili.com/x/msgfeed/at
```

To reply to a Bilibili @ comment:

```text
oid    = item.subject_id
type   = item.business_id
root   = item.root_id or item.source_id
parent = item.source_id
csrf   = cookie.bili_jct
```

Then call:

```text
POST https://api.bilibili.com/x/v2/reply/add
```
