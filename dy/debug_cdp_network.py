from urllib.parse import urlparse
import argparse
import json
import time
import urllib.request

import websocket


DEFAULT_CDP_URL = "http://127.0.0.1:9222"
MAX_PRINT_CHARS = 3000
SENSITIVE_HEADERS = {"cookie", "authorization"}
COMMENT_KEYWORDS = ("comment", "reply", "publish", "challenge")
WS_HOST_KEYWORDS = ("frontier", "bytelink", "douyin")


class CdpClient:
    def __init__(self, browser_ws_url):
        # Chrome rejects arbitrary Origin headers on CDP websocket connections.
        self.ws = websocket.create_connection(browser_ws_url, suppress_origin=True)
        self.next_id = 1
        self.sessions = {}

    def send(self, method, params=None, session_id=None):
        message = {"id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        if session_id:
            message["sessionId"] = session_id
        self.next_id += 1
        self.ws.send(json.dumps(message))

    def close(self):
        self.ws.close()


def clipped(text):
    if text is None:
        return ""
    text = str(text)
    if len(text) <= MAX_PRINT_CHARS:
        return text
    return text[:MAX_PRINT_CHARS] + f"\n... <clipped {len(text) - MAX_PRINT_CHARS} chars>"


def browser_ws_url(cdp_url):
    with urllib.request.urlopen(f"{cdp_url.rstrip('/')}/json/version", timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))["webSocketDebuggerUrl"]


def interesting_url(url, method="GET"):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()

    if host == "www.douyin.com":
        if any(keyword in path for keyword in COMMENT_KEYWORDS):
            return True
        if method.upper() == "POST" and (path.startswith("/aweme/") or path.startswith("/passport/")):
            return True

    if parsed.scheme in {"ws", "wss"} and any(keyword in host for keyword in WS_HOST_KEYWORDS):
        return True

    return False


def sanitize_headers(headers):
    return {
        key: value
        for key, value in (headers or {}).items()
        if key.lower() not in SENSITIVE_HEADERS
    }


def log_request(session_label, params):
    request = params.get("request", {})
    url = request.get("url", "")
    method = request.get("method", "GET")
    if not interesting_url(url, method):
        return

    print("\n[cdp request]")
    print(f"target={session_label}")
    print(url)
    print(f"method={method} type={params.get('type')}")
    print(json.dumps(sanitize_headers(request.get("headers")), ensure_ascii=False, indent=2))
    if request.get("postData"):
        print(f"body={clipped(request['postData'])}")


def log_response(session_label, params):
    response = params.get("response", {})
    url = response.get("url", "")
    request_method = params.get("request", {}).get("method", "GET")
    if not interesting_url(url, request_method):
        return

    print("\n[cdp response]")
    print(f"target={session_label}")
    print(f"status={response.get('status')}")
    print(url)
    headers = sanitize_headers(response.get("headers"))
    content_type = headers.get("content-type") or headers.get("Content-Type")
    if content_type:
        print(f"content-type={content_type}")


def log_ws_created(session_label, params):
    url = params.get("url", "")
    if not interesting_url(url):
        return

    print("\n[cdp websocket created]")
    print(f"target={session_label}")
    print(url)


def log_ws_frame(session_label, direction, params):
    response = params.get("response", {})
    payload = response.get("payloadData", "")
    opcode = response.get("opcode")
    if not payload:
        return

    print(f"\n[cdp websocket frame {direction}]")
    print(f"target={session_label} opcode={opcode} length={len(payload)}")
    print(clipped(payload))


def attach_target(client, target_id, target_type, target_url):
    client.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
    print(f"[attach requested] type={target_type} id={target_id} url={target_url}")


def parse_args():
    parser = argparse.ArgumentParser(description="Chrome CDP 全 target 网络监听。")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    return parser.parse_args()


def main():
    args = parse_args()
    ws_url = browser_ws_url(args.cdp_url)
    print(f"connecting browser cdp: {ws_url}")
    client = CdpClient(ws_url)

    client.send("Target.setDiscoverTargets", {"discover": True})
    client.send(
        "Target.setAutoAttach",
        {
            "autoAttach": True,
            "waitForDebuggerOnStart": False,
            "flatten": True,
        },
    )
    client.send("Target.getTargets")

    try:
        while True:
            message = json.loads(client.ws.recv())
            method = message.get("method")
            params = message.get("params", {})
            session_id = message.get("sessionId")

            if method == "Target.attachedToTarget":
                session_id = params["sessionId"]
                info = params.get("targetInfo", {})
                label = f"{info.get('type')}:{info.get('targetId')}"
                client.sessions[session_id] = label
                print(f"[attached] {label} {info.get('url')}")
                client.send("Network.enable", {}, session_id=session_id)
                client.send("Page.enable", {}, session_id=session_id)
                continue

            if message.get("id") and "result" in message and "targetInfos" in message["result"]:
                for info in message["result"]["targetInfos"]:
                    if info.get("type") in {"page", "worker", "service_worker"}:
                        attach_target(client, info["targetId"], info.get("type"), info.get("url"))
                continue

            label = client.sessions.get(session_id, session_id or "browser")
            if method == "Network.requestWillBeSent":
                log_request(label, params)
            elif method == "Network.responseReceived":
                log_response(label, params)
            elif method == "Network.webSocketCreated":
                log_ws_created(label, params)
            elif method == "Network.webSocketFrameSent":
                log_ws_frame(label, "sent", params)
            elif method == "Network.webSocketFrameReceived":
                log_ws_frame(label, "received", params)

    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        time.sleep(0.1)
        client.close()


if __name__ == "__main__":
    main()
