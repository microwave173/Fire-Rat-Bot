from urllib.parse import parse_qs, urlparse
import argparse
import json
from pathlib import Path
import platform
import socket
import subprocess
import time
import urllib.request

from playwright.sync_api import sync_playwright


DEFAULT_CDP_HOST = "127.0.0.1"
DEFAULT_CDP_PORT = 9222
DEFAULT_USER_DATA_DIR = "dy/chrome_profile"
DEFAULT_START_URL = "https://www.douyin.com/"
NOTICE_PATH = "/aweme/v1/web/notice/"
NOTICE_KEYWORD = "web/notice"
COMMENT_PUBLISH_PATH = "/aweme/v1/web/comment/publish"
COMMENT_KEYWORDS = ("comment", "reply", "publish", "challenge")
WEBSOCKET_KEYWORDS = ("comment", "reply", "publish", "copy", "that", "aweme", "cid")
MAX_PRINT_CHARS = 3000


def find_free_port(host=DEFAULT_CDP_HOST):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def chrome_command(user_data_dir=DEFAULT_USER_DATA_DIR, port=DEFAULT_CDP_PORT):
    user_data_dir = str(Path(user_data_dir).resolve())
    if platform.system() == "Darwin":
        return [
            "open",
            "-na",
            "Google Chrome",
            "--args",
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ]

    return [
        "google-chrome",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-sandbox",
    ]


def wait_for_cdp(port, host=DEFAULT_CDP_HOST, timeout=10):
    url = f"http://{host}:{port}/json/version"
    deadline = time.time() + timeout
    last_error = None

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                data = json.loads(response.read().decode("utf-8"))
            websocket_url = data.get("webSocketDebuggerUrl")
            if websocket_url:
                return websocket_url
            last_error = f"missing webSocketDebuggerUrl in {data}"
        except Exception as exc:
            last_error = exc
        time.sleep(0.3)

    raise RuntimeError(f"Chrome DevTools server did not start at {url}: {last_error}")


def cdp_is_ready(port, host=DEFAULT_CDP_HOST):
    try:
        wait_for_cdp(port, host=host, timeout=1)
        return True
    except Exception:
        return False


def is_notice_url(url):
    return NOTICE_PATH in url or NOTICE_KEYWORD in url


def is_comment_publish_url(url):
    return COMMENT_PUBLISH_PATH in url


def is_interesting_comment_url(url, method="GET"):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()

    if host != "www.douyin.com":
        return False

    if any(keyword in path for keyword in COMMENT_KEYWORDS):
        return True

    # The actual submit path may change, so keep all Douyin business POSTs.
    if method.upper() == "POST" and (path.startswith("/aweme/") or path.startswith("/passport/")):
        return True

    return False


def clipped(text):
    if not text:
        return text
    if len(text) <= MAX_PRINT_CHARS:
        return text
    return text[:MAX_PRINT_CHARS] + f"\n... <clipped {len(text) - MAX_PRINT_CHARS} chars>"


def should_print_ws_payload(payload):
    if isinstance(payload, bytes):
        return True
    lowered = str(payload).lower()
    return any(keyword in lowered for keyword in WEBSOCKET_KEYWORDS)


def format_ws_payload(payload):
    if isinstance(payload, bytes):
        preview = payload[:80].hex()
        return f"<binary {len(payload)} bytes hex={preview}>"
    return clipped(str(payload))


def handle_websocket(ws):
    print("\n[websocket opened]")
    print(ws.url)

    def on_frame_sent(payload):
        if should_print_ws_payload(payload):
            print("\n[websocket frame sent]")
            print(ws.url)
            print(format_ws_payload(payload))

    def on_frame_received(payload):
        if should_print_ws_payload(payload):
            print("\n[websocket frame received]")
            print(ws.url)
            print(format_ws_payload(payload))

    ws.on("framesent", on_frame_sent)
    ws.on("framereceived", on_frame_received)
    ws.on("close", lambda: print(f"\n[websocket closed]\n{ws.url}"))


def attach_page_debug_listeners(page):
    page.on("websocket", handle_websocket)


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
    if is_comment_publish_url(request.url):
        print("\n[comment publish request]")
        print(request.url)
        print(f"method={request.method}")
        headers = {k: v for k, v in request.headers.items() if k.lower() not in {"cookie", "authorization"}}
        print(f"headers={json.dumps(headers, ensure_ascii=False, indent=2)}")
        post_data = request.post_data
        if post_data:
            print(f"body={post_data}")
        return

    if is_interesting_comment_url(request.url, request.method):
        print("\n[interesting request]")
        print(request.url)
        print(f"method={request.method}")
        resource_type = getattr(request, "resource_type", "")
        if resource_type:
            print(f"resource_type={resource_type}")
        headers = {k: v for k, v in request.headers.items() if k.lower() not in {"cookie", "authorization"}}
        print(f"headers={json.dumps(headers, ensure_ascii=False, indent=2)}")
        post_data = request.post_data
        if post_data:
            print(f"body={clipped(post_data)}")
        return

    if not is_notice_url(request.url):
        return

    print("\n[notice request]")
    print(request.url)
    summary = summarize_notice_url(request.url)
    if summary:
        print(f"[notice query] {summary}")


def handle_response(response):
    if is_comment_publish_url(response.url):
        print("\n[comment publish response]")
        print(f"status={response.status}")
        print(response.url)

        try:
            data = response.json()
        except BaseException as exc:
            print(f"[comment publish parse error] {exc}")
            return

        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    if is_interesting_comment_url(response.url, response.request.method):
        print("\n[interesting response]")
        print(f"status={response.status}")
        print(response.url)

        content_type = response.headers.get("content-type", "")
        if "json" not in content_type and "text" not in content_type:
            print(f"content-type={content_type}")
            return

        try:
            text = response.text()
        except BaseException as exc:
            print(f"[interesting response parse error] {exc}")
            return

        if not text:
            print("[empty response body]")
            return

        try:
            data = json.loads(text)
            print(clipped(json.dumps(data, ensure_ascii=False, indent=2)))
        except json.JSONDecodeError:
            print(clipped(text))
        return

    if not is_notice_url(response.url):
        return

    print("\n[notice response]")
    print(f"status={response.status}")
    print(response.url)

    try:
        data = response.json()
    except BaseException as exc:
        print(f"[notice parse error] {exc}")
        return

    print_notice_data(data, prefix="notice")


def print_notice_data(data, prefix="active notice"):
    notices = data.get("notice_list_v2", [])
    print(f"[{prefix} status_code] {data.get('status_code')}")
    print(f"[{prefix} count] {len(notices)}")
    print(f"[{prefix} has_more] {data.get('has_more')}")

    for notice in notices[:5]:
        notice_type = notice.get("type")
        content = notice.get("at", {}).get("content") or notice.get("general_notice", {}).get("content", "")
        print(f"- type={notice_type} nid={notice.get('nid_str') or notice.get('nid')} content={content}")


def extract_cid(schema_url):
    if not schema_url:
        return None
    query = parse_qs(urlparse(schema_url).query)
    values = query.get("cid")
    return values[0] if values else None


def get_at_notices(data):
    at_notices = []
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

        at_notices.append(
            {
                "notice_id": str(notice.get("nid_str") or notice.get("nid")),
                "content": at.get("content", ""),
                "user_name": at.get("user_info", {}).get("nickname", ""),
                "aweme_id": aweme_id,
                "reply_id": reply_id,
                "schema_url": schema_url,
            }
        )
    return at_notices


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
            return { url, ok: response.ok, status: response.status, data };
        }
        """
    )

    print("\n[active notice fetch]")
    print(f"status={result['status']} ok={result['ok']}")
    print(result["url"])
    print_notice_data(result["data"], prefix="active notice")
    return result


def build_reply_payload(notice, text):
    return {
        "aweme_id": notice["aweme_id"],
        "comment_send_celltime": 30000,
        "comment_video_celltime": 120000,
        "one_level_comment_rank": 1,
        "paste_edit_method": "non_paste",
        "reply_id": notice["reply_id"],
        "text": text,
        "text_extra": "[]",
    }


def post_comment_reply(page, notice, text):
    payload = build_reply_payload(notice, text)
    result = page.evaluate(
        """
        async ({ path, payload }) => {
            const targetUrl = new URL(path, window.location.origin);
            const contextParams = {
                app_name: "aweme",
                enter_from: "search_result",
                previous_page: "search_result",
                device_platform: "webapp",
                aid: "6383",
                channel: "channel_pc_web",
                pc_client_type: "1",
                pc_libra_divert: "Mac",
                update_version_code: "170400",
                support_h265: "1",
                support_dash: "1",
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
                    accept: "application/json, text/plain, */*",
                    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "x-secsdk-csrf-token": "DOWNGRADE"
                },
                body: params.toString()
            });

            const responseText = await response.text();
            let data;
            try {
                data = JSON.parse(responseText);
            } catch (error) {
                data = { raw_text: responseText };
            }
            return { ok: response.ok, status: response.status, data };
        }
        """,
        {"path": COMMENT_PUBLISH_PATH, "payload": payload},
    )

    print("\n[reply result]")
    print(f"status={result['status']} ok={result['ok']}")
    print(json.dumps(result["data"], ensure_ascii=False, indent=2))
    return result


def parse_args():
    parser = argparse.ArgumentParser(description="连接真实 Chrome remote debugging 调试 Douyin。")
    parser.add_argument("--cdp-url", default="", help="已有 Chrome remote debugging 地址，例如 http://127.0.0.1:9222。")
    parser.add_argument("--port", type=int, default=0, help="启动 Chrome 的 remote debugging 端口。0 表示自动找空闲端口。")
    parser.add_argument("--user-data-dir", default=DEFAULT_USER_DATA_DIR, help="Chrome 用户目录。")
    parser.add_argument("--url", default=DEFAULT_START_URL, help="打开的 URL。")
    parser.add_argument("--launch-chrome", action="store_true", help="自动启动 Google Chrome remote debugging。")
    parser.add_argument("--fetch-notice", action="store_true", help="连接后主动请求 notice。")
    parser.add_argument("--reply-index", type=int, default=0, help="回复第几个 @ 通知。0 表示不自动回复。")
    parser.add_argument("--reply-text", default="copy that", help="发送的回复内容。")
    parser.add_argument("--yes", action="store_true", help="确认发送回复；没有这个参数时只打印待回复目标。")
    parser.add_argument("--no-wait", action="store_true", help="执行完调试动作后直接退出，不等待 Enter。")
    return parser.parse_args()


def main():
    args = parse_args()
    chrome_proc = None

    if args.launch_chrome:
        port = args.port or DEFAULT_CDP_PORT
        if cdp_is_ready(port):
            cdp_url = wait_for_cdp(port)
            print(f"Chrome DevTools already running: {cdp_url}")
        else:
            cmd = chrome_command(args.user_data_dir, port)
            print("launching chrome:")
            print(" ".join(cmd))
            chrome_proc = subprocess.Popen(cmd)
            cdp_url = wait_for_cdp(port)
    else:
        cdp_url = args.cdp_url or f"http://{DEFAULT_CDP_HOST}:{DEFAULT_CDP_PORT}"
        print("请先手动启动 Chrome，例如：")
        print(" ".join(chrome_command(args.user_data_dir, DEFAULT_CDP_PORT)))
        print(f"然后连接: {cdp_url}")

    with sync_playwright() as p:
        print(f"connecting over cdp: {cdp_url}")
        browser = p.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        context.on("request", handle_request)
        context.on("response", handle_response)
        context.on("page", attach_page_debug_listeners)

        page = context.pages[0] if context.pages else context.new_page()
        attach_page_debug_listeners(page)
        print(f"opening: {args.url}")
        page.goto(args.url, wait_until="domcontentloaded")
        print(f"[browser ua] {page.evaluate('navigator.userAgent')}")
        print(f"[browser platform] {page.evaluate('navigator.platform')}")

        notice_result = None
        if args.fetch_notice or args.reply_index:
            notice_result = active_fetch_notice(page)
            at_notices = get_at_notices(notice_result["data"])
            print_at_notices(at_notices)

            if args.reply_index:
                selected = at_notices[args.reply_index - 1]
                print(
                    f"\n[reply target] index={args.reply_index} "
                    f"nid={selected['notice_id']} user={selected['user_name']} "
                    f"content={selected['content']} text={args.reply_text}"
                )
                if args.yes:
                    post_comment_reply(page, selected, args.reply_text)
                else:
                    print("未发送：需要加 --yes 才会真正回复。")

        print("\nChrome CDP 调试已连接。你可以手动点击 Douyin 页面测试回复。")
        if not args.no_wait:
            try:
                input(">>> 调试完成后按 Enter 断开 Playwright 连接...")
            except EOFError:
                print("stdin closed; disconnecting Playwright.")

        browser.close()

    if chrome_proc:
        print("Chrome 仍可能在运行；如需关闭请手动退出浏览器。")


if __name__ == "__main__":
    main()
