from pathlib import Path
from urllib.parse import parse_qs, urlparse
import argparse
import json

from playwright.sync_api import sync_playwright


DEFAULT_COOKIE_FILE = Path(__file__).with_name("douyin_state.json")
DEFAULT_USER_DATA_DIR = Path(__file__).with_name("douyin_profile")
DEFAULT_START_URL = "https://www.douyin.com/"
NOTICE_PATH = "/aweme/v1/web/notice/"
NOTICE_KEYWORD = "web/notice"
COMMENT_PUBLISH_PATH = "/aweme/v1/web/comment/publish"


def load_cookie_state(cookie_file):
    cookie_path = Path(cookie_file)
    if not cookie_path.exists():
        fallback = Path(__file__).with_name("douyin_cookies.json")
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


def is_notice_url(url):
    return NOTICE_PATH in url or NOTICE_KEYWORD in url


def summarize_notice_url(url):
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    fields = {
        "notice_group": query.get("notice_group", [""])[0],
        "count": query.get("count", [""])[0],
        "is_mark_read": query.get("is_mark_read", [""])[0],
        "min_time": query.get("min_time", [""])[0],
        "max_time": query.get("max_time", [""])[0],
    }
    return " ".join(f"{key}={value}" for key, value in fields.items() if value)


def handle_request(request):
    if not is_notice_url(request.url):
        return

    print("\n[notice request]")
    print(request.url)
    summary = summarize_notice_url(request.url)
    if summary:
        print(f"[notice query] {summary}")


def handle_response(response):
    if not is_notice_url(response.url):
        return

    print("\n[notice response]")
    print(f"status={response.status}")
    print(response.url)

    try:
        data = response.json()
    except Exception as exc:
        print(f"[notice parse error] {exc}")
        return

    notices = data.get("notice_list_v2", [])
    print(f"[notice status_code] {data.get('status_code')}")
    print(f"[notice count] {len(notices)}")

    for notice in notices[:5]:
        notice_type = notice.get("type")
        content = notice.get("at", {}).get("content") or notice.get("general_notice", {}).get("content", "")
        print(f"- type={notice_type} nid={notice.get('nid_str') or notice.get('nid')} content={content}")


def print_notice_data(data):
    notices = data.get("notice_list_v2", [])
    print(f"[active notice status_code] {data.get('status_code')}")
    print(f"[active notice count] {len(notices)}")
    print(f"[active notice has_more] {data.get('has_more')}")

    for notice in notices[:5]:
        notice_type = notice.get("type")
        content = notice.get("at", {}).get("content") or notice.get("general_notice", {}).get("content", "")
        print(f"- type={notice_type} nid={notice.get('nid_str') or notice.get('nid')} content={content}")


def active_fetch_notice(page):
    result = page.evaluate(
        """
        async () => {
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
                pc_libra_divert: "Mac",
                support_h265: "1",
                support_dash: "1",
                cpu_core_num: String(navigator.hardwareConcurrency || 8),
                version_code: "170400",
                version_name: "17.4.0",
                cookie_enabled: String(navigator.cookieEnabled),
                screen_width: String(window.screen.width),
                screen_height: String(window.screen.height),
                browser_language: navigator.language || "zh-CN",
                browser_platform: navigator.platform || "MacIntel",
                browser_name: "Chrome",
                browser_version: "147.0.0.0",
                browser_online: String(navigator.onLine),
                engine_name: "Blink",
                engine_version: "147.0.0.0",
                os_name: "Mac OS",
                os_version: "10.15.7",
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

    print("\n[active notice fetch]")
    print(f"status={result['status']} ok={result['ok']}")
    print(result["url"])
    if isinstance(result.get("data"), dict):
        print_notice_data(result["data"])
    else:
        print(result.get("data"))
    return result


def extract_cid(schema_url):
    if not schema_url:
        return None
    query = parse_qs(urlparse(schema_url).query)
    values = query.get("cid")
    return values[0] if values else None


def get_at_notices(data):
    notices = []
    for notice in data.get("notice_list_v2", []):
        if notice.get("type") != 45 or "at" not in notice:
            continue

        at = notice["at"]
        aweme = at.get("aweme", {})
        schema_url = at.get("schema_url") or notice.get("general_notice", {}).get("schema_url", "")
        aweme_id = str(aweme.get("aweme_id") or notice.get("aweme_id") or "")
        reply_id = extract_cid(schema_url)

        if not aweme_id or not reply_id:
            continue

        notices.append(
            {
                "notice_id": str(notice.get("nid_str") or notice.get("nid")),
                "content": at.get("content", ""),
                "user_name": at.get("user_info", {}).get("nickname", ""),
                "aweme_id": aweme_id,
                "reply_id": reply_id,
                "schema_url": schema_url,
            }
        )
    return notices


def print_at_notices(at_notices):
    if not at_notices:
        print("[at notices] none")
        return

    print("\n[at notices]")
    for index, notice in enumerate(at_notices, start=1):
        print(
            f"{index}. nid={notice['notice_id']} "
            f"user={notice['user_name']} content={notice['content']} "
            f"aweme_id={notice['aweme_id']} reply_id={notice['reply_id']}"
        )


def build_reply_payload(notice, text):
    return {
        "aweme_id": notice["aweme_id"],
        "reply_id": notice["reply_id"],
        "text": text,
        "text_extra": "[]",
        "paste_edit_method": "non_paste",
        "one_level_comment_rank": 1,
        "comment_send_celltime": 30000,
        "comment_video_celltime": 0,
    }


def post_comment_reply(page, notice, text):
    payload = build_reply_payload(notice, text)
    result = page.evaluate(
        """
        async ({ path, payload }) => {
            const params = new URLSearchParams(payload);
            const response = await fetch(path, {
                method: "POST",
                credentials: "include",
                headers: {
                    accept: "application/json, text/plain, */*",
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
        {"path": COMMENT_PUBLISH_PATH, "payload": payload},
    )

    print("\n[reply result]")
    print(f"status={result['status']} ok={result['ok']}")
    print(json.dumps(result["data"], ensure_ascii=False, indent=2))
    return result


def parse_args():
    parser = argparse.ArgumentParser(description="打开有图形界面的 Douyin Playwright 调试浏览器。")
    parser.add_argument("--cookie-file", default=str(DEFAULT_COOKIE_FILE), help="Douyin storage_state 或 Cookie JSON 路径。")
    parser.add_argument("--user-data-dir", default=str(DEFAULT_USER_DATA_DIR), help="持久化 Chromium 用户目录，优先使用。")
    parser.add_argument("--no-persistent-profile", action="store_true", help="不用持久化用户目录，改用 --cookie-file。")
    parser.add_argument("--url", default=DEFAULT_START_URL, help="启动后打开的 URL。")
    parser.add_argument("--wait-ms", type=int, default=3000, help="打开页面后额外等待毫秒数。")
    parser.add_argument("--fetch-notice", action="store_true", help="打开页面后主动在页面 JS 环境里请求 notice 接口。")
    parser.add_argument("--reply-text", default="copy that", help="选择 @ 通知后发送的回复内容。")
    parser.add_argument("--minimal", action="store_true", help="最小调试模式：不监听网络、不主动请求，只复用 profile 打开页面。")
    parser.add_argument("--headed", action="store_true", default=True, help="保留参数占位；脚本默认有图形界面。")
    return parser.parse_args()


def main():
    args = parse_args()

    with sync_playwright() as p:
        if args.no_persistent_profile:
            browser = p.chromium.launch(headless=False)
            context = browser.new_context(
                storage_state=load_cookie_state(args.cookie_file),
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
                locale="zh-CN",
            )
            close_browser = browser.close
            print(f"using cookie/state file: {args.cookie_file}")
        else:
            context = p.chromium.launch_persistent_context(
                user_data_dir=args.user_data_dir,
                headless=False,
            )
            close_browser = context.close
            print(f"using persistent profile: {args.user_data_dir}")

        if not args.minimal:
            context.on("request", handle_request)
            context.on("response", handle_response)
        page = context.new_page()

        print(f"opening: {args.url}")
        page.goto(args.url, wait_until="domcontentloaded")
        page.wait_for_timeout(args.wait_ms)
        print(f"[browser ua] {page.evaluate('navigator.userAgent')}")
        print(f"[browser platform] {page.evaluate('navigator.platform')}")

        cookie_names = {cookie["name"] for cookie in context.cookies("https://www.douyin.com")}
        login_cookie_names = ["sessionid", "sessionid_ss", "sid_tt", "uid_tt"]
        present = [name for name in login_cookie_names if name in cookie_names]
        missing = [name for name in login_cookie_names if name not in cookie_names]
        print(f"[login cookies present] {present}")
        print(f"[login cookies missing] {missing}")

        at_notices = []
        if args.fetch_notice and not args.minimal:
            result = active_fetch_notice(page)
            at_notices = get_at_notices(result["data"])
            print_at_notices(at_notices)

            if at_notices:
                choice = input(">>> 输入要回复的编号；直接 Enter 跳过回复: ").strip()
                if choice:
                    index = int(choice)
                    selected = at_notices[index - 1]
                    print(
                        f"准备回复 #{index}: user={selected['user_name']} "
                        f"content={selected['content']} text={args.reply_text}"
                    )
                    confirm = input(">>> 确认发送请输入 yes: ").strip().lower()
                    if confirm == "yes":
                        post_comment_reply(page, selected, args.reply_text)
                    else:
                        print("已取消发送。")

        print("\n浏览器已打开。你可以手动点击 Douyin 页面。")
        print("脚本会在终端打印捕获到的 notice request/response 和通知数量。")
        input(">>> 调试完成后按 Enter 关闭浏览器...")

        close_browser()


if __name__ == "__main__":
    main()
