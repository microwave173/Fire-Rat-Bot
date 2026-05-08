from urllib.parse import urlparse
import argparse
import base64
import json
import re
import time
import urllib.request

import websocket


DEFAULT_CDP_URL = "http://127.0.0.1:9222"


class CdpClient:
    def __init__(self, browser_ws_url):
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


def browser_ws_url(cdp_url):
    with urllib.request.urlopen(f"{cdp_url.rstrip('/')}/json/version", timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))["webSocketDebuggerUrl"]


def attach_target(client, target_id, target_type, target_url):
    client.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
    print(f"[attach requested] type={target_type} id={target_id} url={target_url}")


def is_douyin_websocket(url):
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    return parsed.scheme in {"ws", "wss"} and "douyin" in host


def extract_json_objects(text):
    decoder = json.JSONDecoder()
    results = []
    for match in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text[match.start() :])
        except json.JSONDecodeError:
            continue
        results.append(obj)
    return results


def decode_payload(response):
    payload = response.get("payloadData", "")
    opcode = response.get("opcode")
    if opcode == 2:
        try:
            raw = base64.b64decode(payload)
            return raw.decode("utf-8", errors="ignore")
        except Exception:
            return ""
    return payload


def looks_like_at_notice(obj):
    text = json.dumps(obj, ensure_ascii=False)
    return (
        '"notice_type": 45' in text
        or '"notice_type":45' in text
        or "comment_at" in text
        or "mentioned you" in text
        or "提到了你" in text
    )


def summarize_notice(obj):
    extra = obj.get("extra") if isinstance(obj.get("extra"), dict) else {}
    extra_str = obj.get("extra_str")
    if isinstance(extra_str, str):
        try:
            extra_from_str = json.loads(extra_str)
        except json.JSONDecodeError:
            extra_from_str = {}
    else:
        extra_from_str = {}

    return {
        "notice_id_str": obj.get("notice_id_str") or obj.get("notice_id") or obj.get("id"),
        "notice_type": obj.get("notice_type"),
        "push_type": obj.get("push_type"),
        "content": obj.get("content") or obj.get("text") or obj.get("content_en"),
        "title": obj.get("title"),
        "comment_id": obj.get("comment_id") or extra.get("comment_id") or extra_from_str.get("comment_id"),
        "item_id": obj.get("item_id") or extra.get("item_id") or extra_from_str.get("aweme_id"),
        "parent_id": extra.get("parent_id"),
        "can_comment": extra.get("can_comment"),
        "author_id": extra_from_str.get("author_id"),
        "notice_category": extra_from_str.get("notice_category"),
        "raw_extra": extra,
        "raw_extra_str": extra_from_str,
    }


def handle_ws_frame(session_label, params):
    text = decode_payload(params.get("response", {}))
    if not text:
        return

    for obj in extract_json_objects(text):
        if not looks_like_at_notice(obj):
            continue
        print("\n[at notice pushed]")
        print(f"target={session_label}")
        print(json.dumps(summarize_notice(obj), ensure_ascii=False, indent=2))
        print("[raw]")
        print(json.dumps(obj, ensure_ascii=False, indent=2))


def parse_args():
    parser = argparse.ArgumentParser(description="Listen Douyin @ notice pushes from Chrome CDP.")
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
        {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True},
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
                continue

            if message.get("id") and "result" in message and "targetInfos" in message["result"]:
                for info in message["result"]["targetInfos"]:
                    if info.get("type") in {"page", "worker", "service_worker"}:
                        attach_target(client, info["targetId"], info.get("type"), info.get("url"))
                continue

            label = client.sessions.get(session_id, session_id or "browser")
            if method == "Network.webSocketCreated":
                url = params.get("url", "")
                if is_douyin_websocket(url):
                    print(f"[douyin websocket] target={label} {url}")
            elif method == "Network.webSocketFrameReceived":
                handle_ws_frame(label, params)
    except KeyboardInterrupt:
        print("\ninterrupted")
    finally:
        time.sleep(0.1)
        client.close()


if __name__ == "__main__":
    main()
