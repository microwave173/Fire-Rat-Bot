import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from channels.douyin import (
    fetch_douyin_notices,
    get_or_create_page,
    load_config,
    make_context,
    normalize_at_notices,
    send_douyin_reply,
)


def parse_args():
    parser = argparse.ArgumentParser(description="Fetch the latest Douyin @ comment and reply once.")
    parser.add_argument("--reply-text", default="copy that")
    parser.add_argument("--index", type=int, default=1, help="1-based index in latest @ notice list.")
    parser.add_argument("--yes", action="store_true", help="Actually send the reply.")
    return parser.parse_args()


def main():
    args = parse_args()
    config = load_config()
    channel_config = config["channels"]["douyin"]
    start_url = channel_config.get("start_url") or "https://www.douyin.com/jingxuan"

    with sync_playwright() as playwright:
        context, close_context = make_context(playwright, channel_config)
        try:
            page = get_or_create_page(context, start_url)
            notices = fetch_douyin_notices(page)
            events = normalize_at_notices(notices)

            if not events:
                print("no douyin @ comment notices found")
                return

            if args.index < 1 or args.index > len(events):
                raise SystemExit(f"--index must be between 1 and {len(events)}")

            event = events[args.index - 1]
            reply_job = {
                "db_id": 0,
                "platform": "douyin",
                "event_id": event["event_id"],
                "reply_text": args.reply_text,
                "raw": event["raw"],
            }

            print("[target]")
            print(json.dumps(
                {
                    "event_id": event["event_id"],
                    "user_name": event["user_name"],
                    "comment_text": event["comment_text"],
                    "video_url": event["video_url"],
                    "raw": event["raw"],
                    "reply_text": args.reply_text,
                },
                ensure_ascii=False,
                indent=2,
            ))

            if not args.yes:
                print("dry run only; add --yes to send")
                return

            result = send_douyin_reply(page, reply_job)
            print("[reply result]")
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            close_context()


if __name__ == "__main__":
    main()
