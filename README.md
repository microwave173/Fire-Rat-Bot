# Fire Rat Bot

一个轻量的多平台 @ 评论自动回复实验项目。当前支持 Bilibili 和 Douyin：

- channel 负责各平台登录态、轮询 @ 消息、发送平台回复。
- gateway 负责统一事件入库、去重、限流、队列和 worker。
- bot_core 负责生成回复内容，目前固定返回 `copy that`。

## 快速运行

以下命令默认在服务器 `/home/ubuntu/at_bot` 中运行，并使用 `python`。

### 1. 启动 gateway

```bash
cd /home/ubuntu/at_bot
. .venv/bin/activate
python gateway.py
```

gateway 会启动本地 HTTP 服务，默认监听 `127.0.0.1:8765`，SQLite 数据库默认写入 `data/at_bot.sqlite3`。

### 2. 启动 Douyin 所需的 Chrome

当前 Douyin 最稳定的方式是复用一份已经登录过的真实 Chrome profile，并通过 CDP 让 channel 在真实浏览器运行时里发请求。

headless 方式：

```bash
cd /home/ubuntu/at_bot
nohup google-chrome --headless=new \
  --remote-debugging-port=9222 \
  --user-data-dir=/home/ubuntu/at_bot/dy/chrome_profile \
  --no-first-run \
  --no-default-browser-check \
  --no-sandbox \
  --disable-gpu \
  >/tmp/chrome-headless.log 2>&1 &
```

确认 CDP 是否可用：

```bash
curl http://127.0.0.1:9222/json/version
```

如果需要图形化登录或人工过验证码，可以启动 noVNC 环境：

```bash
cd /home/ubuntu/at_bot
./run_novnc.sh
```

然后通过端口转发访问 `http://127.0.0.1:6080/vnc.html`。

### 3. 启动 Douyin channel

```bash
cd /home/ubuntu/at_bot
. .venv/bin/activate
python -m channels.douyin
```

Douyin channel 会连接 `config.json` 里的 `channels.douyin.cdp_url`，默认是 `http://127.0.0.1:9222`。

### 4. 启动 Bilibili channel

```bash
cd /home/ubuntu/at_bot
. .venv/bin/activate
python -m channels.bilibili
```

Bilibili channel 使用 `bilibili/bilibili_cookies.json`，通过 `requests` 直接请求 unread、@ 列表和回复接口。

### 5. 用 screen 常驻

```bash
screen -S gateway_s
screen -S channel_dy_s
screen -S bilibili_s
```

恢复 screen：

```bash
screen -r gateway_s
screen -r channel_dy_s
screen -r bilibili_s
```

退出当前 screen 但保持程序运行：按 `Ctrl-a`，再按 `d`。

## 登录态准备

### Bilibili

```bash
python bilibili/get_cookie.py
```

登录后会保存到 `bilibili/bilibili_cookies.json`。这个文件是账号凭证，不提交到 Git。

### Douyin

Douyin channel 推荐使用真实 Chrome profile：`dy/chrome_profile`。如果服务器重启后 profile 仍在，通常只需要重新启动 Chrome/CDP，不需要重新登录。

调试脚本：

```bash
python dy/debug_chrome_cdp.py --launch-chrome --fetch-notice
python dy/test_reply_latest_at.py
python dy/test_reply_latest_at.py --yes
```

`--yes` 会真实发送一条 `copy that`，使用前确认目标评论。

## 配置

主要配置在 `config.json`：

- `gateway.db_path`：SQLite 路径。
- `gateway.worker_count`：生成回复的 worker 数量。
- `gateway.max_queue_size`：队列上限，队列满时新事件会标为已读不回。
- `gateway.event_retention_seconds`：事件保留窗口，避免数据库无限增长。
- `gateway.rate_limit_window_seconds` / `rate_limit_max_mentions`：同一用户限流窗口和次数。
- `channels.bilibili.*`：Bilibili cookie、轮询间隔、启动基线、消息年龄过滤。
- `channels.douyin.cdp_url`：Douyin 连接真实 Chrome 的 CDP 地址。
- `channels.douyin.ignore_existing_on_start`：启动第一轮只建立基线，不回复已有历史 @。
- `channels.douyin.max_notice_age_seconds`：只处理最近的 @。
- `channels.douyin.reply_batch_size` / `reply_interval_seconds`：Douyin 每轮回复数量和间隔。

## 代码结构

```text
.
├── gateway.py                 # 平台无关的 HTTP server、SQLite、队列、限流、状态流转
├── bot_core.py                # 回复生成逻辑；未来接 LLM 时优先改这里
├── config.json                # 关键运行参数
├── channels/
│   ├── common.py              # channel 共享的 gateway HTTP 客户端、配置、cookie 工具
│   ├── bilibili.py            # Bilibili @ 轮询、事件归一化、回复发送
│   └── douyin.py              # Douyin CDP runtime、@ 轮询、事件归一化、回复发送
├── bilibili/
│   ├── get_cookie.py          # 获取 Bilibili cookie
│   └── listen_at_messages.py  # Bilibili @ 消息监听/调试脚本
├── dy/
│   ├── get_cookie.py          # 获取 Douyin cookie/state 的旧调试脚本
│   ├── debug_browser.py       # Playwright 图形化调试
│   ├── debug_chrome_cdp.py    # 连接真实 Chrome CDP 调试
│   ├── debug_cdp_network.py   # 底层 CDP 网络监听
│   ├── listen_at_notices.py   # Douyin @ push/notice 调试
│   └── test_reply_latest_at.py# 读取最新 @，可选发送测试回复
├── DESIGN.md                  # 初版设计说明
├── DEVELOPMENT.md             # 更详细的开发交接文档
└── run_novnc.sh               # 服务器图形化 Chrome/noVNC 启动脚本
```

## 运行流程

1. channel 拉取平台 @ 消息。
2. channel 把平台原始字段归一化成统一事件格式。
3. channel 调 `POST /events` 把事件提交给 gateway。
4. gateway 用 `(platform, event_id)` 去重。
5. gateway 根据配置做用户限流和队列上限判断。
6. worker 调 `bot_core.reply(comment_text, video_url)` 生成回复。
7. gateway 把事件状态改为 `reply_ready`。
8. channel 调 `GET /replies/ready?platform=...` 拉取待回复任务。
9. channel 调平台回复接口。
10. channel 调 `POST /replies/finish` 回写成功或失败。

统一事件格式：

```json
{
  "platform": "douyin",
  "event_id": "平台内唯一事件 ID",
  "user_id": "触发 @ 的用户 ID",
  "user_name": "触发 @ 的用户名",
  "comment_text": "评论内容",
  "video_url": "视频链接",
  "created_at": 1778153815,
  "raw": {}
}
```

## 状态含义

- `new`：事件已入库，等待 worker。
- `processing`：worker 已取走，正在生成回复。
- `reply_ready`：回复文本已生成，等待 channel 发送。
- `replied`：平台回复成功。
- `ignored_rate_limited`：同一用户超过限流，已读不回。
- `dropped_queue_full`：队列满，已读不回。
- `failed`：生成回复或平台发送失败。

## 注意事项

- `bilibili/*cookies*.json`、`dy/*cookies*.json`、`dy/*state*.json`、`dy/chrome_profile/`、`data/` 都是本地运行数据或账号凭证，不提交。
- Douyin 的 `a_bogus`、`msToken` 等签名参数不在代码里逆向，当前依赖真实 Chrome 页面运行时生成。
- Douyin 如果触发验证码，先用 noVNC 打开同一个 `dy/chrome_profile` 人工处理，再继续让 channel 复用这个 profile。
