# 企微智能机器人长连接推送模块设计

**日期**: 2026-05-27
**状态**: 待实施
**范围**: `feedback_hub/ws_bot/` 子包

---

## 1. 背景与目标

### 1.1 背景

当前项目通过 webhook（HTTP POST）方式将每日 Bug 候选推送到企微群。现需改为 WebSocket 长连接方式，利用企微智能机器人 API 实现主动推送。

### 1.2 目标

- 以独立常驻进程方式运行，通过 WebSocket 长连接维持与企微的通信
- 复用现有 `candidate` + `formatter` 模块生成推送内容
- 内置定时调度（每日定时推送）+ 外部 HTTP 手动触发
- 架构上预留接收/回复消息的扩展点

### 1.3 非目标

- 不改动现有 `pusher/webhook.py`（保留为降级通道）
- 当前不实现接收消息并回复（B 能力），仅预留 handler 扩展点
- 不支持多机器人

---

## 2. 架构

### 2.1 模块结构

```
feedback_hub/ws_bot/
├── __init__.py
├── client.py        # WebSocket 生命周期：连接、订阅、心跳、断线重连
├── sender.py        # 封装 aibot_send_msg 主动推送
├── scheduler.py     # asyncio 定时器 + HTTP 手动触发接口
├── handler.py       # 接收 aibot_msg_callback / aibot_event_callback（预留）
├── config.py        # 长连接专属配置
└── __main__.py      # 入口：python -m feedback_hub.ws_bot
```

### 2.2 核心流程

```
启动 → connect(wss://openws.work.weixin.qq.com)
     → aibot_subscribe(bot_id, secret)
     → 启动心跳协程（每 25s ping）
     → 启动定时推送调度
     → 启动 HTTP trigger server
     → event loop 持续运行
         ├─ 收到 pong → 正常
         ├─ 收到 aibot_msg_callback → handler 处理（当前仅记录 chatid）
         ├─ 收到 aibot_event_callback → handler 处理（当前仅记录）
         ├─ 连接断开 → 指数退避重连
         └─ 定时/手动触发 → candidate + formatter → sender 推送
```

### 2.3 与现有代码的关系

| 复用模块 | 说明 |
|----------|------|
| `feedback_hub.db` | 连接 SQLite |
| `feedback_hub.pusher.candidate` | 生成 Top 5 Bug 候选 |
| `feedback_hub.pusher.formatter` | 格式化 Markdown 消息 |
| `feedback_hub.pusher.push_log` | 记录推送日志 |
| `feedback_hub.config.ensure_dirs()` | 确保数据目录 |

`pusher/webhook.py` 保留不动，作为降级通道。

---

## 3. 配置

通过环境变量配置，`ws_bot/config.py` 提供默认值和校验。

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `WECOM_BOT_ID` | 机器人 BotID | （必填） |
| `WECOM_BOT_SECRET` | 长连接 Secret | （必填） |
| `WECOM_CHAT_IDS` | 推送目标群 chatid，逗号分隔 | （可选，支持自动发现） |
| `WECOM_PUSH_CRON` | 推送时间 cron 表达式 | `0 10 * * *` |
| `WECOM_TRIGGER_PORT` | 手动触发 HTTP 端口 | `8081` |
| `WECOM_WS_URL` | WebSocket 服务地址 | `wss://openws.work.weixin.qq.com` |

---

## 4. 连接管理

### 4.1 状态机

```
DISCONNECTED → CONNECTING → SUBSCRIBING → CONNECTED
                                              ↑         |
                                              └── RECONNECTING ←┘
```

### 4.2 订阅请求

连接建立后发送：

```json
{
    "cmd": "aibot_subscribe",
    "headers": { "req_id": "<uuid4>" },
    "body": {
        "bot_id": "<WECOM_BOT_ID>",
        "secret": "<WECOM_BOT_SECRET>"
    }
}
```

收到 `errcode==0` 响应后进入 CONNECTED 状态。

### 4.3 心跳

- 每 **25 秒** 发送 `{"cmd": "ping", "headers": {"req_id": "<uuid>"}}`
- 10 秒内未收到 pong → 主动断开 → 触发重连
- 心跳协程独立于消息循环

### 4.4 重连策略

- 断线后立即尝试第 1 次重连
- 失败后指数退避：1s → 2s → 4s → 8s → 16s → 30s（封顶）
- 加入 ±20% 随机抖动
- 连续失败超过 10 次 → 写 ERROR 日志
- 重连成功后重置退避计数器

---

## 5. 推送逻辑

### 5.1 定时触发

使用 asyncio 定时器，按 `WECOM_PUSH_CRON` 配置的时间触发推送。

### 5.2 推送流程

```
触发
  → 检查连接状态 == CONNECTED
    → 否：写 WARNING 日志，跳过
    → 是：继续
  → candidate.generate_candidates(conn, now_ms)
  → formatter.format_message(...)
  → 遍历 chat_ids，逐个发送 aibot_send_msg
  → 等待每个消息的响应确认（超时 10s）
  → 记录 push_log（成功写 delivered_at，失败写 NULL）
```

### 5.3 发送消息格式

```json
{
    "cmd": "aibot_send_msg",
    "headers": { "req_id": "<uuid>" },
    "body": {
        "chatid": "<target_chatid>",
        "chat_type": 1,
        "msgtype": "markdown",
        "markdown": { "content": "<markdown 内容>" }
    }
}
```

### 5.4 频率限制

遵守官方限制：30 条/分钟，1000 条/小时。当前场景（每日 1 次，推送到少数群）远低于限制。

---

## 6. 手动触发接口

轻量 aiohttp HTTP server，单个端点：

```
POST http://localhost:{WECOM_TRIGGER_PORT}/trigger
Content-Type: application/json (可选)
Body: {"date": "2026-05-27"}  // 可选，默认今天
```

响应：

| 状态码 | 含义 |
|--------|------|
| `200 {"status": "ok", "pushed": <n>}` | 推送成功 |
| `503 {"status": "disconnected"}` | WebSocket 未连接 |
| `500 {"status": "error", "detail": "..."}` | 推送失败 |

### 6.1 CLI 集成

`cli.py` 新增选项：

```
python -m feedback_hub.cli push --via ws [--date 2026-05-27]
```

内部 HTTP 请求到 trigger 端点，统一 CLI 入口体验。

---

## 7. chatid 自动发现

- 首次启动若 `WECOM_CHAT_IDS` 未配置，进入"学习模式"
- 收到 `aibot_msg_callback` 或 `enter_chat` 事件时提取 `chatid`
- 持久化到 `feedback_hub/data/chat_ids.json`
- 后续启动优先读环境变量，其次读本地文件
- 文件格式：

```json
{
    "chat_ids": ["chatid_001", "chatid_002"],
    "updated_at": "2026-05-27T10:00:00"
}
```

---

## 8. 日志与可观测性

| 事件 | 级别 | 格式 |
|------|------|------|
| 连接成功 | INFO | `[ws_bot] connected and subscribed` |
| 心跳超时 | WARNING | `[ws_bot] pong timeout, reconnecting` |
| 推送成功 | INFO | `[ws_bot] pushed to {chatid}, req_id={id}` |
| 推送失败 | ERROR | `[ws_bot] send failed: errcode={code} errmsg={msg}` |
| 重连尝试 | WARNING | `[ws_bot] reconnect attempt {n}, backoff={sec}s` |
| 重连失败 | ERROR | `[ws_bot] reconnect failed after {n} attempts` |
| chatid 发现 | INFO | `[ws_bot] discovered chatid: {id}` |
| 定时触发 | INFO | `[ws_bot] scheduled push triggered` |
| 手动触发 | INFO | `[ws_bot] manual push triggered via HTTP` |

日志输出到 stderr，格式与现有模块一致。

---

## 9. 依赖新增

| 包 | 用途 | 版本约束 |
|----|------|----------|
| `websockets` | 异步 WebSocket 客户端 | `>=12.0` |
| `aiohttp` | 轻量 HTTP server（trigger 端点） | `>=3.9` |

---

## 10. 扩展预留（B 能力）

`handler.py` 预留以下接口：

```python
async def on_message(payload: dict) -> None:
    """处理 aibot_msg_callback。当前仅记录 chatid，后续可扩展为回复逻辑。"""
    ...

async def on_event(payload: dict) -> None:
    """处理 aibot_event_callback。当前仅日志记录。"""
    ...
```

后续迭代 B 能力时，只需填充这两个函数，无需改动连接层。

---

## 11. 启动方式

```bash
# 必填环境变量
export WECOM_BOT_ID="<企微机器人 ID>"
export WECOM_BOT_SECRET="<企微机器人 Secret>"

# 可选
export WECOM_CHAT_IDS="chatid_001"
export WECOM_PUSH_CRON="0 10 * * *"

# 启动常驻进程
python -m feedback_hub.ws_bot

# 手动触发推送
python -m feedback_hub.cli push --via ws
# 或直接 HTTP
curl -X POST http://localhost:8081/trigger
```
