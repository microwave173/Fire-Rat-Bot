from pathlib import Path
import json
import os
import urllib.error
import urllib.request


DEEPSEEK_API_URL = "https://api.deepseek.com/chat/completions"
DEEPSEEK_MODEL = "deepseek-v4-flash"
FALLBACK_REPLY = "喵，猫猫收到啦～"


def load_local_env(path=".env"):
    """Load simple KEY=VALUE lines for local/server runs without extra deps."""
    env_path = Path(path)
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def build_messages(comment_text, video_url):
    return [
        {
            "role": "system",
            "content": (
                "你是一个叫“猫猫”的猫娘回复机器人。"
                "你的任务是在评论区自然、友好、轻快地回复用户。"
                "保持中文回复，像猫猫一样偶尔用“喵”，但不要过度卖萌。"
                "回复长度控制在一到三句话，不要解释自己是 AI，不要使用 Markdown。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户评论：{comment_text}\n"
                f"视频链接：{video_url or '无'}\n"
                "请用猫猫的人设回复这条评论。"
            ),
        },
    ]


def call_deepseek(comment_text, video_url):
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return FALLBACK_REPLY

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": build_messages(comment_text, video_url),
        "temperature": 1.3,
        "max_tokens": 160,
        "stream": False,
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        DEEPSEEK_API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"DeepSeek API failed: {exc.code} {error_body}") from exc

    content = data["choices"][0]["message"]["content"].strip()
    return content or FALLBACK_REPLY


def reply(comment_text, video_url):
    """Return the text that should be posted as a reply."""
    load_local_env()
    return call_deepseek(comment_text, video_url)
