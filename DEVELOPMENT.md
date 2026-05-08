# 开发说明

这份文档用于后续开发和交接，说明当前项目的目录职责、主流程和关键代码位置。

## 目录与文件

```text
.
├── README.md
├── DESIGN.md
├── DEVELOPMENT.md
├── config.json
├── bot_core.py
├── gateway.py
├── channels/
│   ├── __init__.py
│   ├── common.py
│   ├── douyin.py
│   └── bilibili.py
├── bilibili/
│   ├── get_cookie.py
│   ├── bilibili_cookies.json
│   └── note.txt
└── dy/
    ├── get_cookie.py
    ├── douyin_cookies.json
    ├── douyin_state.json
    ├── douyin_profile/
    └── note.txt
```

### `README.md`

面向使用者的快速启动说明。

常用命令：

```bash
python gateway.py
python -m channels.bilibili
python -m channels.douyin
```

### `DESIGN.md`

轻量设计文档，记录最初的模块拆分、状态机、限流策略和 Bilibili 接口映射规则。

### `DEVELOPMENT.md`

当前这份文档，面向后续开发和交接。

### `config.json`

项目的关键配置文件。

主要配置：

- `gateway.host` / `gateway.port`：gateway HTTP 服务地址。
- `gateway.db_path`：SQLite 数据库路径。
- `gateway.worker_count`：处理 bot_core 的 worker 数量。
- `gateway.max_queue_size`：gateway 内部队列上限。
- `gateway.event_retention_seconds`：数据库事件保留时间，防止无限增长。
- `gateway.rate_limit_window_seconds`：用户限流统计窗口。
- `gateway.rate_limit_max_mentions`：窗口内同一用户最多处理多少次 @。
- `channels.bilibili.cookie_file`：Bilibili Cookie JSON 文件路径。
- `channels.bilibili.unread_poll_base_seconds`：unread 探针基础轮询间隔。
- `channels.bilibili.unread_poll_jitter_seconds`：轮询随机抖动。
- `channels.bilibili.at_fetch_min_interval_seconds`：拉取完整 @ 列表的最小间隔。
- `channels.douyin.cookie_file`：Douyin Playwright storage_state 路径，推荐 `dy/douyin_state.json`。
- `channels.douyin.user_data_dir`：Douyin 持久化 Chromium 用户目录，推荐 `dy/douyin_profile`。写操作优先复用它。
- `channels.douyin.start_url`：Douyin channel 启动时打开的页面。
- `channels.douyin.headless`：Douyin channel 是否用无图形界面的 Playwright Chromium。
- `channels.douyin.poll_base_seconds`：Douyin 通知轮询基础间隔。
- `channels.douyin.poll_jitter_seconds`：Douyin 通知轮询随机抖动。
- `channels.douyin.ignore_existing_on_start`：启动后的第一轮只建立基线，不提交已有通知，避免批量回复历史 @。
- `channels.douyin.max_notice_age_seconds`：只处理最近多少秒内产生的 @ 通知。
- `channels.douyin.reply_batch_size`：每轮最多发送多少条 Douyin 回复，避免短时间集中发送。
- `channels.douyin.reply_interval_seconds`：同一轮批量回复时，两条回复之间的基础间隔。

### `bot_core.py`

平台无关的回复生成逻辑。

当前只有一个关键函数：

```python
def reply(comment_text, video_url):
    return "copy that"
```

后续接 LLM 时，优先改这里。建议保持输入简单：

- `comment_text`：用户评论内容。
- `video_url`：评论所在视频链接。

平台账号、Cookie、回复 API、数据库状态等逻辑不要放到 `bot_core.py`。

### `gateway.py`

平台无关的调度层，负责 HTTP server、SQLite、去重、限流、队列和 worker。

它不直接调用 Bilibili 接口，只处理标准化后的事件。

关键类：

- `EventStore`：SQLite 读写封装。
- `GatewayApp`：业务调度逻辑。
- `Handler`：HTTP 接口处理。

关键函数和方法：

- `load_config()`：读取 `config.json`。
- `EventStore._init_db()`：初始化 SQLite 表结构。
- `EventStore.insert_event(...)`：写入事件，并通过 `(platform, event_id)` 去重。
- `EventStore.mention_count_in_window(...)`：统计同一用户在限流窗口内的 @ 次数。
- `EventStore.cleanup_old_events()`：删除过期事件，防止数据库无限增长。
- `GatewayApp.submit_events(...)`：接收一批标准化事件。
- `GatewayApp.submit_one_event(...)`：单条事件的去重、限流、入队逻辑。
- `GatewayApp.worker_loop()`：后台 worker 主循环。
- `GatewayApp.process_event(...)`：调用 `bot_core.reply(...)`，生成回复文本。
- `GatewayApp.ready_replies(...)`：返回已生成、等待平台发送的回复任务。
- `GatewayApp.finish_reply(...)`：channel 发送平台回复后，回写最终状态。

HTTP 接口：

- `GET /health`：健康检查。
- `POST /events`：channel 提交标准化事件。
- `GET /replies/ready?platform=<platform>`：channel 拉取待发送的回复。
- `POST /replies/finish`：channel 回写平台回复是否成功。

事件状态：

- `new`：事件已入库，等待处理。
- `processing`：worker 已取走，正在调用 `bot_core`。代码中视为已读。
- `reply_ready`：`bot_core` 已生成回复，等待 channel 调平台回复接口。
- `replied`：平台回复成功。
- `ignored_rate_limited`：用户超过限流，已读不回。
- `dropped_queue_full`：队列满，已读不回。
- `failed`：`bot_core` 或平台回复失败。

### `channels/bilibili.py`

Bilibili 平台接入层。

职责：

- 读取 `bilibili/bilibili_cookies.json`。
- 请求 Bilibili unread 接口，判断是否有新的 @。
- 有 @ 时，请求完整 @ 列表。
- 把 Bilibili 原始数据转换成 gateway 能理解的标准化事件。
- 从 gateway 拉取 `reply_ready` 任务。
- 调 Bilibili 回复接口。
- 把回复结果回写 gateway。

关键函数：

- `make_session(cookie_file)`：创建带 Cookie 和基础 headers 的 `requests.Session`。
- `check_unread_at(session)`：请求 Bilibili unread 接口，返回 `data.at`。
- `fetch_at_messages(session)`：请求完整 @ 列表。
- `normalize_at_item(item)`：把 Bilibili @ item 转成标准事件。
- `fetch_ready_replies()`：从 gateway 拉取待发送回复。
- `build_bilibili_reply_payload(reply_job, csrf)`：把 gateway 回复任务转成 Bilibili 回复 payload。
- `send_bilibili_reply(session, reply_job)`：调用 Bilibili 回复接口。
- `finish_reply(db_id, ok, error=None)`：回写 gateway 状态。
- `process_ready_replies(session)`：批量处理所有待发送回复。
- `main()`：Bilibili channel 主循环。

Bilibili 回复字段映射：

```text
oid    = item.subject_id
type   = item.business_id
root   = item.root_id or item.source_id
parent = item.source_id
csrf   = cookie.bili_jct
```

### `channels/douyin.py`

Douyin 平台接入层。

职责：

- 优先使用 `dy/douyin_profile` 持久化浏览器目录。
- 如果未配置 `user_data_dir`，才读取 `dy/douyin_state.json`，不存在时退回 `dy/douyin_cookies.json`。
- 请求 Douyin notice 接口。
- 只处理 `type=45` 的 @ 评论通知。
- 把 Douyin 原始通知转换成 gateway 能理解的标准化事件。
- 从 gateway 拉取 `reply_ready` 任务。
- 调 Douyin 评论回复接口。
- 把回复结果回写 gateway。

关键类和函数：

- `make_context(...)`：创建 Playwright Chromium context，并加载 Cookie。
- `fetch_douyin_notices(page)`：在 Douyin 页面运行时中主动请求 notice 接口。
- `normalize_at_notices(notices)`：过滤并转换 @ 通知。
- `normalize_notice(notice)`：把单条 Douyin @ 通知转成标准事件。
- `normalize_web_notice(notice)`：处理网页版 notice 结构。
- `normalize_push_notice(notice)`：处理移动端 push 同步到网页后的扁平结构。
- `extract_cid(schema_url)`：从 `aweme://...?...cid=xxx` 里提取评论 ID。
- `browser_fetch_json(page, path, payload)`：在页面 JS 环境里发 POST 请求。
- `build_douyin_reply_payload(reply_job)`：把 gateway 回复任务转成 Douyin 回复 payload。
- `send_douyin_reply(page, reply_job)`：调用 Douyin 回复接口。
- `process_ready_replies(page)`：批量处理待发送回复。
- `main()`：Douyin channel 主循环。

Douyin 回复字段映射：

```text
aweme_id = notice.at.aweme.aweme_id
reply_id = notice.at.schema_url 里的 cid
text     = bot_core 返回的 reply_text
```

Douyin channel 当前优先通过 `cdp_url` 连接一份已经登录的真实 Chrome。回复前会进入 `https://www.douyin.com/jingxuan?modal_id=<aweme_id>`，再在页面 JS 环境里发送评论回复请求。这样可以复用网页运行时自动生成的 `a_bogus`、`msToken`、`bd-ticket-guard-*`、`x-tt-session-dtrait` 等风控参数。

当前实现仍然是实验版：真实网页连续回复可能触发验证码或“操作过于频繁”。如果出现这类风控，需要在 noVNC 的真实 Chrome 中人工验证，channel 继续复用同一个浏览器 profile。

批量自动化策略：

- 每轮先拉取 @ 通知，标准化后提交给 gateway。
- 默认第一轮只建立基线，不提交当前 notice 列表里已有的历史 @。
- 默认只处理最近 `max_notice_age_seconds` 内产生的 @。
- gateway 负责去重、限流、调用 `bot_core` 生成回复。
- Douyin channel 再拉取 `reply_ready` 任务，按 `reply_batch_size` 分批发送。
- 每条回复之间等待 `reply_interval_seconds + 0~3 秒随机抖动`。
- 当前不逆向 `a_bogus` 等签名算法，而是依赖真实 Chrome 页面运行时生成签名和安全头。

### `channels/common.py`

不同平台 channel 共享的小工具。

主要包括：

- `load_config()`：读取配置。
- `gateway_url(config)`：拼 gateway 地址。
- `submit_events(...)`：提交标准事件到 gateway。
- `fetch_ready_replies(...)`：拉取指定平台待回复任务。
- `finish_reply(...)`：回写平台回复结果。
- `load_playwright_cookie_jar(...)`：读取 Playwright Cookie JSON。
- `get_cookie_value(...)`：按名称读取 Cookie。

## 标准事件格式

所有 channel 都应该把平台原始数据转换成同一套格式再提交 gateway：

```json
{
  "platform": "bilibili 或 douyin",
  "event_id": "平台内唯一事件 ID",
  "user_id": "触发 @ 的用户 ID",
  "user_name": "触发 @ 的用户名",
  "comment_text": "评论内容",
  "video_url": "视频链接",
  "created_at": 1778153815,
  "raw": {}
}
```

gateway 只依赖这些通用字段：

- `platform`
- `event_id`
- `user_id`
- `user_name`
- `comment_text`
- `video_url`
- `created_at`
- `raw`

`raw` 由各平台自己保存回复所需字段。gateway 不解析 `raw`，只会原样存储并在 `reply_ready` 时交还给对应 channel。

### `bilibili/get_cookie.py`

用 Playwright 打开 Chromium，让用户手动登录 Bilibili，然后保存 Cookie JSON。

默认输出：

```text
bilibili/bilibili_cookies.json
```

多账号时可以指定不同文件：

```bash
python bilibili/get_cookie.py -o bilibili/bilibili_cookies_main.json
python bilibili/get_cookie.py -o bilibili/bilibili_cookies_alt.json
```

### `bilibili/note.txt`

人工抓包记录。里面保存了当前研究过的 Bilibili curl、响应样例和字段对应关系。

不要把真实 Cookie 提交或分享出去。

### `dy/get_cookie.py`

用 Playwright 打开 Chromium，让用户手动登录 Douyin，然后保存 Cookie JSON。

默认输出：

```text
dy/douyin_cookies.json
dy/douyin_state.json
dy/douyin_profile/
```

多账号时可以指定不同文件：

```bash
python dy/get_cookie.py -o dy/douyin_cookies_main.json --state-output dy/douyin_state_main.json --user-data-dir dy/douyin_profile_main
python dy/get_cookie.py -o dy/douyin_cookies_alt.json --state-output dy/douyin_state_alt.json --user-data-dir dy/douyin_profile_alt
```

### `dy/note.txt`

人工抓包记录。里面保存了 Douyin curl、响应样例和字段对应关系。

不要把真实 Cookie、签名 URL、Token 提交或分享出去。

## 主运行流程

1. 启动 gateway：

   ```bash
   python gateway.py
   ```

2. 启动 Bilibili channel：

   ```bash
   python -m channels.bilibili
   ```

   或启动 Douyin channel：

   ```bash
   python -m channels.douyin
   ```

3. channel 定时请求平台通知。

4. 如果发现新的 @，channel 拉取完整 @ 列表或通知列表。

5. channel 标准化事件并 POST 到 gateway `/events`。

6. gateway 做以下处理：

   ```text
   已存在 event_id -> duplicate，跳过
   用户超过一小时 3 次 -> ignored_rate_limited
   队列满 -> dropped_queue_full
   正常 -> new -> 入队
   ```

7. worker 取出事件，状态改为 `processing`，调用 `bot_core.reply(...)`。

8. 当前 `bot_core` 返回 `copy that`，gateway 把状态改为 `reply_ready`。

9. channel 拉取 `/replies/ready?platform=<platform>`。

10. channel 调平台回复接口。

11. channel 调 `/replies/finish` 回写结果：

    ```text
    成功 -> replied
    失败 -> failed
    ```

## 后续开发建议

- 接 LLM：优先改 `bot_core.py`，保持 gateway 和 channel 不动。
- 加平台：新增 `channels/<platform>.py`，实现类似 `normalize_at_item` 和平台回复逻辑。
- 加管理界面：优先读 gateway 的 SQLite 或新增只读 HTTP 查询接口。
- 加重试：可以从 `failed` 状态入手，增加手动或定时重试逻辑。
- 调风控参数：优先改 `config.json`，不要硬编码到业务逻辑里。
