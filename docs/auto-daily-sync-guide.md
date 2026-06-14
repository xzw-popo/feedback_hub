# 每日自动同步：开发与运维指南

> 最后更新：2026-06-14
> 设计文档：[docs/superpowers/specs/2026-06-13-auto-daily-sync-design.md](superpowers/specs/2026-06-13-auto-daily-sync-design.md)

---

## 一、功能概述

每日自动同步流水线实现了「本地拉取 → 打标 → 导出 → 云端导入 → 推送」的全流程自动化，不再需要重建 Docker 镜像来更新数据。

### 数据流

```
┌─────────────── 本地 Mac ───────────────────────┐
│                                                │
│  launchd 每天 10:00 触发                        │
│    ↓                                           │
│  VPN 检测（最多等待 30 分钟）                    │
│    ↓                                           │
│  pull --date 昨天（需 iOA VPN）                 │
│    ↓                                           │
│  tag（离线模式 + 在线 LLM 降级）                │
│    ↓                                           │
│  exporter.py → data/export/YYYY-MM-DD.json     │
│    ↓                                           │
│  POST /api/import（带 3 次重试）                │
│    ↓                                           │
│  push（可选，企微推送）                         │
│                                                │
└────────────────────┬───────────────────────────┘
                     │ HTTPS + Bearer Token
                     ▼
┌─────────────── CloudBase 云端 ─────────────────┐
│  CloudRun (FastAPI)                            │
│    ├─ /api/*           只读 API（已有）        │
│    └─ POST /api/import 数据导入（新增）        │
│         ↓                                      │
│  CloudBase MySQL (VPC 内网)                    │
└────────────────────────────────────────────────┘
```

### 时间窗口规则

**拉取/导出的是"昨天一整天"的数据，使用绝对日期而非相对时间。**

```
脚本运行日期 = T
数据窗口 = T-1 整天（北京时间 00:00:00 ~ 23:59:59.999）

示例：
  6月14日运行 → 拉取/同步 6月13日的数据
  6月15日运行 → 拉取/同步 6月14日的数据
```

选择绝对日期的原因：
- `--last 24h` 会因执行时间不同导致窗口漂移
- 绝对日期保证每天的数据边界清晰，无重叠无遗漏
- UPSERT 天然幂等，同一天重复运行不产生重复数据

---

## 二、文件说明

| 文件 | 用途 | 运行环境 |
|------|------|----------|
| `feedback_hub/importer.py` | CloudRun 导入 API（`POST /api/import`） | 云端 |
| `feedback_hub/exporter.py` | 本地 SQLite → JSON 导出 | 本地 |
| `feedback_hub/auto_daily.sh` | 全流程自动化主脚本 | 本地 |
| `com.feedback_hub.daily_sync.plist` | macOS launchd 定时配置 | 本地 |

### 修改的现有文件

| 文件 | 改动 |
|------|------|
| `feedback_hub/api.py` | 注册 import router；CORS `allow_methods` 增加 `POST` |
| `feedback_hub/config.py` | 新增 `IMPORT_TOKEN` 配置项（从环境变量读取） |

---

## 三、导入 API（importer.py）

### 端点

```
POST /api/import
```

### 请求格式

```json
{
  "token": "<IMPORT_TOKEN>",
  "tables": {
    "feedback": [
      {"feedback_id": "xxx", "ts_ms": 1718000000000, "platform": "android", ...}
    ],
    "message_label": [
      {"feedback_id": "xxx", "l1": "A.Bug", "l2": "输入核心", "severity": "P1", ...}
    ],
    "conversation_label": [
      {"conversation_id": "xxx", "l1": "A.Bug", ...}
    ]
  }
}
```

### 处理逻辑

1. **鉴权**：校验 `token` 与环境变量 `IMPORT_TOKEN` 匹配，不匹配返回 401
2. **模式检查**：仅在 MySQL 模式下可用（`DB_MODE=mysql`）
3. **逐表 UPSERT**：`INSERT ... ON DUPLICATE KEY UPDATE`，500 行/批
4. **降级插入**：批量失败时自动逐条插入，定位问题行
5. **单表隔离**：一张表写入失败不影响其他表继续

### 返回格式

成功：
```json
{
  "ok": true,
  "imported": {
    "feedback": {"upserted": 1523, "errors": 0},
    "message_label": {"upserted": 1523, "errors": 0},
    "conversation_label": {"upserted": 310, "errors": 0}
  }
}
```

部分失败（`ok: false` 但仍是 200）：
```json
{
  "ok": false,
  "imported": {
    "feedback": {"upserted": 1500, "errors": 23},
    "message_label": {"upserted": 1523, "errors": 0},
    "conversation_label": {"upserted": 310, "errors": 0}
  }
}
```

### 错误码

| 状态码 | 含义 |
|--------|------|
| 200 | 全部成功或部分成功 |
| 400 | 非 MySQL 模式 |
| 401 | token 不匹配 |
| 405 | 非 POST 方法 |
| 422 | 请求体格式错误 |
| 500 | MySQL 连接失败等严重错误 |

### 安全措施

- `IMPORT_TOKEN` 通过 CloudRun 环境变量注入，不硬编码
- 仅响应 POST 方法
- 字段白名单：只接受 `feedback`、`message_label`、`conversation_label` 三张表的列定义

---

## 四、导出模块（exporter.py）

### 用法

```bash
# 导出指定日期的数据（默认输出到 data/export/YYYY-MM-DD.json）
python -m feedback_hub.exporter --date 2026-06-12

# 指定输出路径
python -m feedback_hub.exporter --date 2026-06-12 --output /tmp/export.json

# 写入 token（方便直接 POST 到 import API）
python -m feedback_hub.exporter --date 2026-06-12 --token "your_import_token"
```

### 查询逻辑

| 表 | 时间筛选字段 | 说明 |
|----|------------|------|
| `feedback` | `ts_ms` | 直接按时间戳范围筛选 |
| `message_label` | JOIN `feedback.ts_ms` | message_label 没有时间列，通过 JOIN feedback 获取时间 |
| `conversation_label` | `last_ts_ms` | 按会话最后更新时间筛选 |

### 输出格式

与 `POST /api/import` 请求体完全一致：

```json
{
  "token": "（如果 --token 指定则写入，否则空字符串）",
  "tables": {
    "feedback": [...],
    "message_label": [...],
    "conversation_label": [...]
  }
}
```

---

## 五、自动化脚本（auto_daily.sh）

### 用法

```bash
# 拉取昨天的数据并同步到云端
bash feedback_hub/auto_daily.sh

# 指定日期补跑（如补跑 6月11日的数据）
bash feedback_hub/auto_daily.sh --date 2026-06-11

# 仅导出不同步（调试用）
bash feedback_hub/auto_daily.sh --dry-run
```

### 执行流程

```
1. 幂等检查
   └─ last_sync.json 记录的日期 == 目标日期 → 跳过

2. VPN 检测（最多 6 次，每次间隔 5 分钟，共 30 分钟）
   └─ 超时 → macOS 通知 + 退出

3. pull --date $DATE
   └─ 失败 → 通知 + 退出

4. tag（离线+在线模式）
   └─ 失败 → 通知 + 退出

5. exporter.py → data/export/$DATE.json
   └─ 失败 → 通知 + 退出

6. POST /api/import（3 次重试，10s 间隔）
   └─ 失败 → 通知 + 退出

7. push --date $DATE（可选，失败不影响同步结果）

8. 更新 last_sync.json → macOS 通知
```

### 环境变量

| 变量 | 必需 | 默认值 | 说明 |
|------|------|--------|------|
| `IMPORT_TOKEN` | 是 | 无 | 导入 API 鉴权密钥 |
| `FEEDBACK_API_URL` | 否 | CloudRun 公网地址 | 后端 API 地址 |

### 日志文件

| 文件 | 说明 |
|------|------|
| `feedback_hub/data/logs/auto_daily.log` | 每次执行的详细日志 |
| `feedback_hub/data/logs/launchd_stdout.log` | launchd 标准输出 |
| `feedback_hub/data/logs/launchd_stderr.log` | launchd 标准错误 |
| `feedback_hub/data/export/last_sync.json` | 上次成功同步的日期 |
| `feedback_hub/data/export/YYYY-MM-DD.json` | 每日导出的 JSON 数据 |

---

## 六、macOS 定时调度（launchd）

### 配置文件

`~/Library/LaunchAgents/com.feedback_hub.daily_sync.plist`

```xml
关键配置：
- Label: com.feedback_hub.daily_sync
- 触发时间: 每天 10:00（StartCalendarInterval: Hour=10, Minute=0）
- RunAtLoad: false（开盖时不补跑）
- 环境变量: IMPORT_TOKEN + FEEDBACK_API_URL
```

选择 10:00 的原因：只有个人电脑，需开盖 + VPN 连通；数据是昨天的，早几小时晚几小时无影响。

### 安装/卸载

```bash
# 安装定时任务
launchctl load ~/Library/LaunchAgents/com.feedback_hub.daily_sync.plist

# 卸载定时任务
launchctl unload ~/Library/LaunchAgents/com.feedback_hub.daily_sync.plist

# 查看状态
launchctl list | grep feedback_hub

# 立即触发一次（调试用）
launchctl start com.feedback_hub.daily_sync
```

### 前提条件

launchd 执行的 bash 需要 **Full Disk Access** 权限才能访问 Desktop 目录：

1. 系统设置 → 隐私与安全性 → 完全磁盘访问权限
2. 点 `+` → `Cmd+Shift+G` 输入 `/bin/bash` → 选中并启用

---

## 七、CloudRun 部署配置

### 环境变量（v021+）

在原有环境变量基础上新增：

```
IMPORT_TOKEN=<随机生成的密钥>
```

完整 CloudRun 环境变量清单：

```
DB_MODE=mysql
MYSQL_HOST=172.17.0.7
MYSQL_PORT=3306
MYSQL_USER=feedback
MYSQL_PASSWORD=12345678tencent.
MYSQL_DATABASE=feedback7-d3gz69ofw321c4da5
CORS_ORIGINS=https://feedback7-d3gz69ofw321c4da5-1442771950.tcloudbaseapp.com
PORT=9000
IMPORT_TOKEN=<你的密钥>
```

### 部署方式

通过 CloudBase MCP 或控制台上传代码包部署。代码包中需包含：
- `Dockerfile`、`entrypoint.sh`、`requirements.txt`
- `feedback_hub/`（排除 `data/`、`tests/`、`__pycache__/`、`ws_bot/`、`raw/`、`export/`、`logs/`）

### 当前版本

- CloudRun 服务：`feedback-api` v021
- 1 CPU / 2GB Mem / Min=Max=1 实例

---

## 八、故障排查

### 常见问题

| 现象 | 原因 | 解决方法 |
|------|------|----------|
| `Operation not permitted` | launchd 的 bash 没有 Desktop 访问权限 | 给 `/bin/bash` 添加 Full Disk Access |
| `IMPORT_TOKEN 环境变量未配置` | CloudRun 未设 IMPORT_TOKEN | 在 CloudBase 控制台添加环境变量 |
| `token 不匹配` | 本地脚本的 token 与云端不一致 | 检查 plist 和 CloudRun 环境变量中的 IMPORT_TOKEN |
| `导入 API 仅在 MySQL 模式下可用` | DB_MODE 未设置为 mysql | 确认 CloudRun 环境变量 DB_MODE=mysql |
| VPN 检测超时 | 电脑未连 VPN 或 iOA 未启动 | 手动连接 VPN，然后手动执行脚本 |
| 部分数据导入失败 | MySQL 列类型/长度不匹配 | 查看 CloudRun 日志中 `[import]` 开头的错误行 |

### 手动排查步骤

```bash
# 1. 检查上次同步状态
cat feedback_hub/data/export/last_sync.json

# 2. 查看执行日志
tail -50 feedback_hub/data/logs/auto_daily.log

# 3. 查看 launchd 日志
tail -50 feedback_hub/data/logs/launchd_stderr.log

# 4. 测试导入 API 连通性
curl -s -X POST \
  -H "Content-Type: application/json" \
  -d '{"token":"your_token","tables":{}}' \
  https://feedback-api-269678-9-1442771950.sh.run.tcloudbase.com/api/import

# 5. 手动导出并同步
python -m feedback_hub.exporter --date 2026-06-13 --token "your_token"
curl -s -X POST -H "Content-Type: application/json" \
  -d @feedback_hub/data/export/2026-06-13.json \
  https://feedback-api-269678-9-1442771950.sh.run.tcloudbase.com/api/import
```

---

## 九、实施过程中遇到的问题

### 9.1 requirements.txt 缺少 sse-starlette

**现象**：v020 部署后容器启动失败，日志 `ModuleNotFoundError: No module named 'sse_starlette'`

**原因**：智能搜索功能（search API）依赖 `sse-starlette`，但 `requirements.txt` 中未声明

**解决**：添加 `sse-starlette>=2.0` 到 `requirements.txt`，v021 修复

### 9.2 launchd 执行 bash 报 Operation not permitted

**现象**：launchd 触发脚本后，日志显示 `/bin/bash: auto_daily.sh: Operation not permitted`

**原因**：macOS TCC 隐私保护，Desktop 目录对 launchd 启动的进程不可见

**解决**：在系统设置 → 隐私与安全性 → 完全磁盘访问权限中添加 `/bin/bash`

### 9.3 批量 UPSERT 偶发 500 错误

**现象**：全量 curl 导入时偶尔返回 `{"detail":"(0, '')"}`

**原因**：pymysql 连接在批量写入时可能被断开

**解决**：
1. importer.py 增加降级逻辑：批量失败时逐条插入，定位问题行
2. auto_daily.sh 的 curl 增加 3 次重试，10s 间隔

### 9.4 CloudBase MySQL _openid 列约束

**现象**：INSERT 失败 `Field '_openid' doesn't have a default value`

**原因**：CloudBase MySQL 自动添加 `_openid varchar(64) NOT NULL` 列

**解决**：已在之前部署时对所有表执行 ALTER TABLE 修改为 DEFAULT NULL

---

## 十、与旧同步方式的关系

| 旧方式 | 新方式 | 说明 |
|--------|--------|------|
| `sync_to_cloud.py` | `exporter.py` + `/api/import` | 旧方式需 VPC 内网，新方式通过公网 HTTPS |
| Docker 镜像打入 SQLite | JSON POST 导入 | 旧方式需重建镜像 + 3 次部署，新方式无需改镜像 |
| `bootstrap_sync.py` | 保留 | 全量初始化场景仍可用（如新建环境） |

旧文件保留不删除，但不再作为日常同步路径。
