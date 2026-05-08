from pathlib import Path
from urllib.parse import parse_qs, urlparse
import json
import random
import time

from playwright.sync_api import sync_playwright

from channels.common import (
    fetch_ready_replies,
    finish_reply,
    gateway_url,
    load_config,
    submit_events,
)


PLATFORM = "douyin"
GATEWAY_URL = None
NOTICE_PATH = "/aweme/v1/web/notice/"
COMMENT_PUBLISH_PATH = "/aweme/v1/web/comment/publish"
DEFAULT_CDP_URL = "http://127.0.0.1:9222"


def load_cookie_state(cookie_file):
    """Load either Playwright storage_state or a plain cookies JSON list."""
    cookie_path = Path(cookie_file)
    if not cookie_path.exists():
        fallback = Path("dy/douyin_cookies.json")
        if fallback.exists():
            print(f"state file not found, fallback to cookie file: {fallback}")
            cookie_path = fallback
        else:
            raise FileNotFoundError(f"missing douyin state/cookie file: {cookie_path}. Run: python dy/get_cookie.py")

    with cookie_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and "cookies" in data:
        return data
    if isinstance(data, list):
        return {"cookies": data, "origins": []}
    raise ValueError(f"unsupported cookie state format: {cookie_file}")


def make_context(playwright, channel_config):
    cdp_url = channel_config.get("cdp_url")
    if cdp_url:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        if not browser.contexts:
            raise RuntimeError(f"connected to {cdp_url}, but no browser context was found")
        print(f"using douyin chrome cdp: {cdp_url}")
        # Do not close the real Chrome process when the channel exits. The CDP
        # connection will be torn down with Playwright's context manager.
        return browser.contexts[0], lambda: None

    user_data_dir = channel_config.get("user_data_dir")
    headless = channel_config.get("headless", True)

    if user_data_dir:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        print(f"using douyin persistent profile: {user_data_dir}")
        return context, context.close

    browser = playwright.chromium.launch(
        headless=headless,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )
    context = browser.new_context(
        storage_state=load_cookie_state(channel_config["cookie_file"]),
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        locale="zh-CN",
    )
    print(f"using douyin state file: {channel_config['cookie_file']}")
    return context, browser.close


def get_or_create_page(context, start_url):
    if context.pages:
        page = context.pages[0]
    else:
        page = context.new_page()

    current_url = page.url or ""
    if not current_url.startswith("https://www.douyin.com"):
        page.goto(start_url, wait_until="domcontentloaded", timeout=30000)
    return page


def parse_notice_data(data):
    if data.get("status_code") != 0:
        raise RuntimeError(f"douyin notice api returned error: {data}")

    notices = data.get("notice_list_v2", [])
    print(f"captured {len(notices)} douyin notices")
    return notices


def fetch_douyin_notices(page):
    result = page.evaluate(
        """
        async () => {
            const chromeVersion = ((navigator.userAgent.match(/Chrome\\/([0-9.]+)/) || [])[1] || "147.0.0.0");
            const isLinux = navigator.platform && navigator.platform.includes("Linux");
            const params = new URLSearchParams({
                device_platform: "webapp",
                aid: "6383",
                channel: "channel_pc_web",
                is_new_notice: "1",
                is_mark_read: "1",
                notice_group: "960",
                count: "10",
                min_time: "0",
                max_time: "0",
                update_version_code: "170400",
                pc_client_type: "1",
                pc_libra_divert: isLinux ? "Linux" : "Mac",
                support_h265: isLinux ? "0" : "1",
                support_dash: "1",
                cpu_core_num: String(navigator.hardwareConcurrency || 8),
                version_code: "170400",
                version_name: "17.4.0",
                cookie_enabled: String(navigator.cookieEnabled),
                screen_width: String(window.screen.width),
                screen_height: String(window.screen.height),
                browser_language: navigator.language || "zh-CN",
                browser_platform: navigator.platform || (isLinux ? "Linux x86_64" : "MacIntel"),
                browser_name: "Chrome",
                browser_version: chromeVersion,
                browser_online: String(navigator.onLine),
                engine_name: "Blink",
                engine_version: chromeVersion,
                os_name: isLinux ? "Linux" : "Mac OS",
                os_version: isLinux ? "x86_64" : "10.15.7",
                device_memory: String(navigator.deviceMemory || 8),
                platform: "PC",
                downlink: String((navigator.connection && navigator.connection.downlink) || 10),
                effective_type: (navigator.connection && navigator.connection.effectiveType) || "4g",
                round_trip_time: String((navigator.connection && navigator.connection.rtt) || 50)
            });

            const url = `/aweme/v1/web/notice/?${params.toString()}`;
            const response = await fetch(url, {
                method: "GET",
                credentials: "include",
                headers: {
                    accept: "application/json, text/plain, */*",
                    "accept-language": "zh-CN"
                }
            });

            const text = await response.text();
            let data;
            try {
                data = JSON.parse(text);
            } catch (error) {
                data = { raw_text: text };
            }

            return {
                url,
                ok: response.ok,
                status: response.status,
                data
            };
        }
        """
    )

    if not result["ok"]:
        raise RuntimeError(f"douyin notice http failed: {result['status']} {result['data']}")

    print(f"active douyin notice fetch: {result['url']}")
    return parse_notice_data(result["data"])


def extract_cid(schema_url):
    if not schema_url:
        return None
    query = parse_qs(urlparse(schema_url).query)
    values = query.get("cid")
    return values[0] if values else None


def douyin_video_url(aweme_id):
    return f"https://www.douyin.com/video/{aweme_id}"


def notice_id(notice):
    return str(
        notice.get("nid_str")
        or notice.get("nid")
        or notice.get("notice_id_str")
        or notice.get("notice_id")
        or notice.get("id")
    )


def normalize_web_notice(notice):
    at = notice["at"]
    user = at.get("user_info", {})
    aweme = at.get("aweme", {})
    schema_url = at.get("schema_url") or notice.get("general_notice", {}).get("schema_url", "")
    aweme_id = str(aweme.get("aweme_id") or notice.get("aweme_id") or "")
    reply_id = extract_cid(schema_url)

    if not aweme_id or not reply_id:
        raise ValueError(f"missing aweme_id or reply_id in notice {notice.get('nid_str')}")

    return {
        "platform": PLATFORM,
        "event_id": notice_id(notice),
        "user_id": str(user.get("uid") or notice.get("user_id")),
        "user_name": user.get("nickname", ""),
        "comment_text": at.get("content", ""),
        "video_url": douyin_video_url(aweme_id),
        "created_at": int(notice.get("create_time") or time.time()),
        "raw": {
            "aweme_id": aweme_id,
            "reply_id": reply_id,
            "notice_id": notice_id(notice),
            "schema_url": schema_url,
            "aweme_desc": aweme.get("desc", ""),
        },
    }


def normalize_push_notice(notice):
    aweme_id = str(notice.get("item_id") or notice.get("aweme_id") or "")
    reply_id = str(notice.get("comment_id") or "")
    extra = notice.get("extra") if isinstance(notice.get("extra"), dict) else {}

    if not aweme_id or not reply_id:
        raise ValueError(f"missing item_id or comment_id in notice {notice_id(notice)}")

    return {
        "platform": PLATFORM,
        "event_id": notice_id(notice),
        "user_id": str(notice.get("from_uid") or notice.get("user_id") or ""),
        "user_name": notice.get("title") or notice.get("sender_name") or "",
        "comment_text": (
            notice.get("content", "")
            .replace("<b>提到了你：</b>", "")
            .replace("<b>mentioned you:</b>", "")
            .strip()
        ),
        "video_url": douyin_video_url(aweme_id),
        "created_at": int(notice.get("create_time") or notice.get("push_time") or time.time()),
        "raw": {
            "aweme_id": aweme_id,
            "reply_id": reply_id,
            "notice_id": notice_id(notice),
            "schema_url": f"aweme://aweme/detail/{aweme_id}?cid={reply_id}",
            "aweme_desc": notice.get("aweme_desc", ""),
            "source": "push_notice",
            "can_comment": extra.get("can_comment"),
            "comment_permission_status": extra.get("comment_permission_status"),
            "parent_id": extra.get("parent_id"),
        },
    }


def normalize_notice(notice):
    if "at" in notice:
        return normalize_web_notice(notice)
    return normalize_push_notice(notice)


def normalize_at_notices(notices):
    events = []
    for notice in notices:
        # In the captured response, type=45 is an @ comment notice.
        notice_type = notice.get("type") or notice.get("notice_type")
        if notice_type != 45:
            continue
        try:
            events.append(normalize_notice(notice))
        except Exception as exc:
            print(f"skip douyin notice {notice.get('nid_str')}: {exc}")
    return events


def filter_recent_events(events, max_age_seconds):
    if not max_age_seconds:
        return events

    cutoff = int(time.time()) - int(max_age_seconds)
    recent_events = [event for event in events if int(event.get("created_at") or 0) >= cutoff]
    skipped = len(events) - len(recent_events)
    if skipped:
        print(f"skip {skipped} old douyin events older than {max_age_seconds}s")
    return recent_events


def event_ids(events):
    return {str(event["event_id"]) for event in events}


def only_new_events(events, seen_ids):
    new_events = [event for event in events if str(event["event_id"]) not in seen_ids]
    skipped = len(events) - len(new_events)
    if skipped:
        print(f"skip {skipped} already seen douyin events")
    seen_ids.update(event_ids(events))
    return new_events


def build_douyin_reply_payload(reply_job):
    raw = reply_job["raw"]
    return {
        "aweme_id": raw["aweme_id"],
        # These are browser-side timing metrics. Small, human-like values match
        # the successful requests captured from the real Douyin UI.
        "comment_send_celltime": str(random.randint(3000, 12000)),
        "comment_video_celltime": str(random.randint(2500, 70000)),
        "one_level_comment_rank": "1",
        "paste_edit_method": "non_paste",
        "reply_id": raw["reply_id"],
        "text": reply_job["reply_text"],
        "text_extra": "[]",
    }


def browser_fetch_json(page, path, payload):
    return page.evaluate(
        """
        async ({ path, payload }) => {
            const targetUrl = new URL(path, window.location.origin);
            const isLinux = navigator.platform && navigator.platform.includes("Linux");
            const contextParams = {
                app_name: "aweme",
                enter_from: "discover",
                previous_page: "discover",
                device_platform: "webapp",
                aid: "6383",
                channel: "channel_pc_web",
                pc_client_type: "1",
                pc_libra_divert: isLinux ? "Linux" : "Mac",
                update_version_code: "170400",
                support_h265: isLinux ? "0" : "1",
                support_dash: "1",
                version_code: "170400",
                version_name: "17.4.0",
                cookie_enabled: String(navigator.cookieEnabled),
                screen_width: String(window.screen.width),
                screen_height: String(window.screen.height),
                browser_language: navigator.language || "zh-CN",
                browser_platform: navigator.platform || "MacIntel",
                browser_name: "Chrome",
                browser_version: ((navigator.userAgent.match(/Chrome\\/([0-9.]+)/) || [])[1] || "147.0.0.0"),
                browser_online: String(navigator.onLine),
                engine_name: "Blink",
                engine_version: ((navigator.userAgent.match(/Chrome\\/([0-9.]+)/) || [])[1] || "147.0.0.0"),
                os_name: isLinux ? "Linux" : "Mac OS",
                os_version: isLinux ? "x86_64" : "10.15.7",
                cpu_core_num: String(navigator.hardwareConcurrency || 8),
                device_memory: String(navigator.deviceMemory || 8),
                platform: "PC",
                downlink: String((navigator.connection && navigator.connection.downlink) || 10),
                effective_type: (navigator.connection && navigator.connection.effectiveType) || "4g",
                round_trip_time: String((navigator.connection && navigator.connection.rtt) || 50)
            };

            for (const [key, value] of Object.entries(contextParams)) {
                targetUrl.searchParams.set(key, value);
            }

            const params = new URLSearchParams(payload);
            const response = await fetch(targetUrl.toString(), {
                method: "POST",
                credentials: "include",
                headers: {
                    "accept": "application/json, text/plain, */*",
                    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "x-secsdk-csrf-token": "DOWNGRADE"
                },
                body: params.toString()
            });

            const text = await response.text();
            let data;
            try {
                data = JSON.parse(text);
            } catch (error) {
                data = { raw_text: text };
            }

            return {
                ok: response.ok,
                status: response.status,
                data
            };
        }
        """,
        {"path": path, "payload": payload},
    )


def is_empty_response(result):
    data = result.get("data") or {}
    return result.get("ok") and data.get("raw_text") == ""


def fetch_reply_once(page, reply_job):
    return browser_fetch_json(page, COMMENT_PUBLISH_PATH, build_douyin_reply_payload(reply_job))


def send_douyin_reply(page, reply_job):
    aweme_id = reply_job["raw"]["aweme_id"]
    modal_url = f"https://www.douyin.com/jingxuan?modal_id={aweme_id}"
    if page.url != modal_url:
        page.goto(modal_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    result = fetch_reply_once(page, reply_job)
    if is_empty_response(result):
        print(f"douyin reply empty response, retry once: {reply_job['event_id']}")
        page.reload(wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        result = fetch_reply_once(page, reply_job)

    if not result["ok"]:
        raise RuntimeError(f"douyin reply http failed: {result['status']} {result['data']}")

    data = result["data"]
    if not data.get("comment"):
        raise RuntimeError(f"douyin reply api failed: {data}")
    return data


def process_ready_replies(page, channel_config):
    batch_size = int(channel_config.get("reply_batch_size", 5))
    reply_interval = float(channel_config.get("reply_interval_seconds", 8))
    ready_replies = fetch_ready_replies(GATEWAY_URL, PLATFORM)

    if len(ready_replies) > batch_size:
        print(f"douyin has {len(ready_replies)} ready replies, processing first {batch_size}")

    for index, reply_job in enumerate(ready_replies[:batch_size], start=1):
        try:
            send_douyin_reply(page, reply_job)
            finish_reply(GATEWAY_URL, reply_job["db_id"], True)
            print(f"replied douyin event {reply_job['event_id']}")
        except Exception as exc:
            finish_reply(GATEWAY_URL, reply_job["db_id"], False, str(exc))
            print(f"failed to reply douyin event {reply_job['event_id']}: {exc}")

        if index < min(len(ready_replies), batch_size):
            time.sleep(reply_interval + random.uniform(0, 3))


def sleep_seconds(config):
    base = config["poll_base_seconds"]
    jitter = config["poll_jitter_seconds"]
    return max(1, base + random.uniform(-jitter, jitter))


def main():
    global GATEWAY_URL
    config = load_config()
    channel_config = config["channels"]["douyin"]
    GATEWAY_URL = gateway_url(config)

    start_url = channel_config.get("start_url") or "https://www.douyin.com/"
    headless = channel_config.get("headless", True)
    ignore_existing_on_start = channel_config.get("ignore_existing_on_start", True)
    max_notice_age_seconds = channel_config.get("max_notice_age_seconds")
    print(f"douyin channel starting with playwright headless={headless}")

    with sync_playwright() as playwright:
        context, close_context = make_context(playwright, channel_config)
        try:
            page = get_or_create_page(context, start_url)
            print("douyin channel started")
            seen_event_ids = set()

            if ignore_existing_on_start:
                try:
                    baseline_notices = fetch_douyin_notices(page)
                    baseline_events = normalize_at_notices(baseline_notices)
                    seen_event_ids.update(event_ids(baseline_events))
                    print(
                        "baseline douyin startup: marked "
                        f"{len(seen_event_ids)} existing events as read without reply"
                    )
                except Exception as exc:
                    print(f"baseline douyin startup failed: {exc}")

            while True:
                try:
                    notices = fetch_douyin_notices(page)
                    events = normalize_at_notices(notices)
                    events = filter_recent_events(events, max_notice_age_seconds)
                    events = only_new_events(events, seen_event_ids)

                    if events:
                        result = submit_events(GATEWAY_URL, events)
                        print(f"submitted {len(events)} douyin events: {result}")
                    else:
                        print("no douyin at notices")

                    process_ready_replies(page, channel_config)

                except Exception as exc:
                    print(f"douyin channel error: {exc}")

                time.sleep(sleep_seconds(channel_config))
        finally:
            close_context()


if __name__ == "__main__":
    main()
