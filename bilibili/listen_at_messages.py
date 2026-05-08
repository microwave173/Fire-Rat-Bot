import argparse
import json
import sys
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from channels.bilibili import (  # noqa: E402
    check_unread_at,
    fetch_at_messages,
    make_session,
    normalize_at_item,
)
from channels.common import load_config  # noqa: E402


def summarize_item(item):
    detail = item.get("item", {})
    user = item.get("user", {})
    normalized = normalize_at_item(item)

    return {
        "id": item.get("id"),
        "at_time": item.get("at_time"),
        "user": {
            "mid": user.get("mid"),
            "nickname": user.get("nickname"),
        },
        "detail": {
            "source_content": detail.get("source_content"),
            "uri": detail.get("uri"),
            "native_uri": detail.get("native_uri"),
            "subject_id": detail.get("subject_id"),
            "business_id": detail.get("business_id"),
            "source_id": detail.get("source_id"),
            "root_id": detail.get("root_id"),
            "target_id": detail.get("target_id"),
            "title": detail.get("title"),
        },
        "normalized": normalized,
    }


def print_item(item, raw=False):
    print("\n[bilibili at]")
    print(json.dumps(summarize_item(item), ensure_ascii=False, indent=2))
    if raw:
        print("[raw]")
        print(json.dumps(item, ensure_ascii=False, indent=2))


def parse_args():
    parser = argparse.ArgumentParser(description="Listen Bilibili @ messages without replying.")
    parser.add_argument("--interval", type=float, default=5, help="Polling interval seconds.")
    parser.add_argument("--raw", action="store_true", help="Print full raw API item.")
    parser.add_argument("--fetch-on-start", action="store_true", help="Fetch and print current @ list immediately.")
    return parser.parse_args()


def main():
    args = parse_args()
    config = load_config()
    channel_config = config["channels"]["bilibili"]
    session = make_session(channel_config["cookie_file"])
    seen_ids = set()

    print("bilibili @ listener started; no replies will be sent")
    print(f"poll interval: {args.interval}s")

    if args.fetch_on_start:
        items = fetch_at_messages(session)
        print(f"initial fetch: {len(items)} items")
        for item in items:
            seen_ids.add(str(item.get("id")))
            print_item(item, raw=args.raw)

    while True:
        try:
            unread_at = check_unread_at(session)
            print(f"unread at: {unread_at}")
            if unread_at > 0:
                items = fetch_at_messages(session)
                new_items = []
                for item in items:
                    item_id = str(item.get("id"))
                    if item_id in seen_ids:
                        continue
                    seen_ids.add(item_id)
                    new_items.append(item)

                print(f"fetched {len(items)} items, new {len(new_items)}")
                for item in new_items:
                    print_item(item, raw=args.raw)

        except Exception as exc:
            print(f"bilibili listener error: {exc}")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
