from pathlib import Path
import json

import requests


CONFIG_PATH = Path("config.json")


def load_config():
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def gateway_url(config):
    gateway = config["gateway"]
    return f"http://{gateway['host']}:{gateway['port']}"


def submit_events(gateway_base_url, events):
    response = requests.post(f"{gateway_base_url}/events", json={"events": events}, timeout=15)
    response.raise_for_status()
    return response.json()


def fetch_ready_replies(gateway_base_url, platform):
    response = requests.get(
        f"{gateway_base_url}/replies/ready",
        params={"platform": platform},
        timeout=15,
    )
    response.raise_for_status()
    return response.json().get("items", [])


def finish_reply(gateway_base_url, db_id, ok, error=None):
    response = requests.post(
        f"{gateway_base_url}/replies/finish",
        json={"db_id": db_id, "ok": ok, "error": error},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def load_playwright_cookie_jar(cookie_file):
    cookie_file = Path(cookie_file)
    with cookie_file.open("r", encoding="utf-8") as f:
        cookies = json.load(f)

    jar = requests.cookies.RequestsCookieJar()
    for cookie in cookies:
        jar.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain"),
            path=cookie.get("path", "/"),
        )
    return jar


def get_cookie_value(session, name):
    for cookie in session.cookies:
        if cookie.name == name:
            return cookie.value
    return None
