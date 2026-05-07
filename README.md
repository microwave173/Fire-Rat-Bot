# at_bot

Small auto-reply bot for comments that mention the account.

## Prepare Bilibili Cookies

```bash
python bilibili/get_cookie.py
```

This opens Chromium. Log in manually, then press Enter in the terminal.
The cookie file is saved to `bilibili/bilibili_cookies.json`.

## Run

Start the gateway:

```bash
python gateway.py
```

In another terminal, start the Bilibili channel:

```bash
python -m channels.bilibili
```

Current `bot_core` always returns:

```text
copy that
```

## Config

Edit `config.json` for polling interval, queue size, SQLite path, and per-user
rate limit.
