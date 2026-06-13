# 每日自动同步设计文档

> 日期：2026-06-13
> 状态：已确认

## 1. 背景

当前反馈数据更新流程完全手动：pull（需 iOA VPN）→ tag → 重新打包 Docker 镜像 → 部署 CloudRun → bootstrap_sync 导入 MySQL。每次更新至少需要 3 次部署操作，耗时 15~30 分钟。

核心瓶颈：

- pull 和 tag 依赖内网 API，必须 VPN 连通
- CloudBase MySQL（172.17.0.7）仅 VPC 内可访问，外网无法直连
- 当前数据同步路径是"SQLite 打入 Docker 镜像 → 部署 → bootstrap_sync"，极其繁琐

目标：**一条命令（或自动定时）完成 pull → tag → 同步 → push 全流程，不再需要重建 Docker 镜像。**

## 2. 方案选择

评估了三种方案：

| 方案 | 思路 | 优势 | 劣势 |
|------|------|------|------|
| **A. CloudRun 导入 API** | 本地导出 JSON → HTTPS POST → CloudRun 写 MySQL | 无需改网络配置、安全可控、复用已有基础 | 需开发导入 API |
| B. MySQL 开外网访问 | 本地直连 MySQL 外网地址 | 改动最小 | CloudBase 可能不支持、安全风险高 |
| C. 自动重建 Docker | 每次更新重新 build+deploy | 无需新 API | 耗时长、SQLite 随数据膨胀 |

**选定方案 A**：在 CloudRun FastAPI 上新增 `/api/import` 端点，本地通过公网 HTTPS 调用写入 MySQL。

## 3. 整体架构

```
┌──────────── 本地 Mac ────────────────┐    ┌──── CloudBase 云端 ────────────┐
│                                      │    │                                │
│  launchd 每日 10:00 触发              │    │  CloudRun (FastAPI)            │
│    ↓                                 │    │    ├─ /api/*  (只读, 已有)     │
│  VPN 检测 (等待连通, 最多 30 分钟)     │    │    └─ /api/import (写入, 新增) │
│    ↓                                 │    │         ↓                      │
│  pull --date 昨天                    │    │  CloudBase MySQL (VPC 内网)    │
│    ↓                                 │    │                                │
│  tag (离线模式)                       │    └────────────────────────────────┘
│    ↓                                 │         ↑
│  exporter.py (SQLite → JSON)         │ ────────┘ HTTPS POST
│    ↓                                 │  (Bearer token 鉴权)
│  POST /api/import                    │
│    ↓                                 │
│  push (可选, 企微推送)                │
│                                      │
└──────────────────────────────────────┘
```

## 4. 时间窗口规则

**拉取/导出的是"昨天一整天"的数据，使用绝对日期而非相对时间。**

```
脚本运行日期 = T
拉取/导出时间窗口 = T-1 整天（北京时间 00:00:00 ~ 23:59:59.999）

示例：
  脚本 6月13日运行 → 拉取 6月12日 00:00~23:59:59 的数据
  脚本 6月14日运行 → 拉取 6月13日 00:00~23:59:59 的数据
```

选择绝对日期的原因：

- `--last 24h` 会因执行时间不同导致窗口漂移
- 绝对日期保证每天的数据边界清晰，无重叠无遗漏
- pull 使用 `--date YYYY-MM-DD`，tag 和 exporter 按同一日期筛选
- UPSERT 天然幂等，同一天重复运行不产生重复数据

## 5. CloudRun 导入 API

### 5.1 端点

```
POST /api/import
```

### 5.2 请求格式

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

### 5.3 处理逻辑

1. **鉴权**：校验 `token` 与环境变量 `IMPORT_TOKEN` 是否匹配，不匹配返回 401
2. **逐表 UPSERT**：对每个 table 中的数据执行 `INSERT ... ON DUPLICATE KEY UPDATE`，复用 `sync_to_cloud.py` 已有的批量写入逻辑
3. **批量提交**：每 500 行一批，与现有同步脚本保持一致
4. **单表隔离**：一张表写入失败不影响其他表继续

### 5.4 返回格式

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

部分失败：

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

### 5.5 错误码

| 状态码 | 含义 |
|--------|------|
| 200 | 全部成功或部分成功 |
| 401 | token 不匹配 |
| 405 | 非 POST 方法 |
| 422 | 请求体格式错误 |
| 500 | MySQL 连接失败等严重错误 |

### 5.6 安全措施

- `IMPORT_TOKEN` 通过 CloudRun 环境变量注入，不硬编码
- 导入 API 仅响应 POST，GET 返回 405
- 请求体大小限制：20MB（日常增量约 2~5MB，留足余量）

## 6. 本地自动化脚本

### 6.1 主脚本：`feedback_hub/auto_daily.sh`

```
1. 检测 VPN 连通性
   ├─ 不通 → 等待 5 分钟重试，最多重试 6 次（30 分钟）
   └─ 通 → 继续

2. pull --date $(昨天日期)
   └─ 失败 → 记录错误，终止

3. tag（离线模式）
   └─ 失败 → 记录错误，终止

4. exporter.py（SQLite → JSON）
   ├─ 查询昨天的 feedback / message_label / conversation_label
   ├─ 输出到 data/export/YYYY-MM-DD.json
   └─ 失败 → 记录错误，终止

5. POST /api/import
   ├─ 读取 JSON，发送到 CloudRun API
   ├─ 校验返回结果
   └─ 失败 → 记录错误，终止

6. push（可选步骤）
   └─ 生成并推送每日 Top Bug 报告到企微

7. 记录执行日志到 data/logs/auto_daily.log
8. macOS 通知中心弹出执行结果
```

### 6.2 导出模块：`feedback_hub/exporter.py`

- 从 SQLite 查询指定日期的三张表数据
- 查询条件：`WHERE date(ts_ms/1000, 'unixepoch', '+8 hours') = 'YYYY-MM-DD'`
- 输出格式与 `/api/import` 请求体一致
- 写入 `data/export/YYYY-MM-DD.json`

### 6.3 日志与状态

- 每次执行记录：时间、步骤、成功/失败、行数统计
- 状态文件 `data/export/last_sync.json`：记录上次成功同步的日期（如 `"2026-06-12"`）
- 如果当天已成功同步过，跳过本次执行（幂等）

### 6.4 错误通知

- 脚本失败时，通过 macOS 通知中心弹出提醒（`osascript -e 'display notification'`）
- 成功时也弹通知，显示导入行数

### 6.5 手动触发

```bash
# 一键手动执行（与自动调度走同一个脚本，拉取昨天的数据）
bash feedback_hub/auto_daily.sh

# 指定日期补跑
bash feedback_hub/auto_daily.sh --date 2026-06-11

# 仅同步不上传（调试用）
bash feedback_hub/auto_daily.sh --dry-run
```

## 7. macOS 定时调度

### 7.1 launchd 配置

文件：`~/Library/LaunchAgents/com.feedback_hub.daily_sync.plist`

```xml
关键配置：
- Label: com.feedback_hub.daily_sync
- 每天北京时间 10:00 触发
- StartCalendarInterval: { Hour: 10, Minute: 0 }
- RunAtLoad: false
- StandardOutPath: feedback_hub/data/logs/launchd_stdout.log
- StandardErrorPath: feedback_hub/data/logs/launchd_stderr.log
```

选择 10:00 的原因：

- 只有个人电脑，需要开盖+VPN 连通
- 10:00 是合理的工作时间
- 数据是昨天的，早几小时晚几小时无影响

### 7.2 安装/卸载

```bash
# 安装定时任务
launchctl load ~/Library/LaunchAgents/com.feedback_hub.daily_sync.plist

# 卸载定时任务
launchctl unload ~/Library/LaunchAgents/com.feedback_hub.daily_sync.plist

# 查看状态
launchctl list | grep feedback_hub
```

## 8. 文件变更清单

### 新增文件

| 文件 | 用途 |
|------|------|
| `feedback_hub/importer.py` | CloudRun 导入 API 端点（FastAPI router） |
| `feedback_hub/exporter.py` | 本地 SQLite → JSON 导出模块 |
| `feedback_hub/auto_daily.sh` | 本地自动化主脚本 |
| `com.feedback_hub.daily_sync.plist` | macOS launchd 定时配置文件 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `feedback_hub/api.py` | 注册 importer router，挂载到 `/api/import` |
| `feedback_hub/config.py` | 新增 `IMPORT_TOKEN` 配置项（从环境变量读取） |
| `feedback_hub/db.py` | 新增批量 UPSERT 辅助函数（供 importer 调用） |

### CloudRun 环境变量新增

| 变量名 | 说明 |
|--------|------|
| `IMPORT_TOKEN` | 导入 API 鉴权密钥，同时配到本地脚本的环境变量中 |

### 不改动的部分

- 前端 `dashboard/`：无变更，数据更新后自动刷新
- `sync_to_cloud.py`：保留但不再作为主要同步路径
- `bootstrap_sync.py`：保留，全量初始化场景仍可用
- `ws_bot/`：保持独立，push 由 `auto_daily.sh` 调用触发
- `Dockerfile`：无变更
- `entrypoint.sh`：无变更

## 9. 实施顺序

1. 开发 `importer.py`（导入 API）+ 修改 `api.py`/`db.py`/`config.py`
2. 本地测试导入 API（通过 curl 或 httpie 发送测试数据）
3. 开发 `exporter.py`（导出模块）
4. 开发 `auto_daily.sh`（自动化主脚本）
5. 部署 CloudRun（新增 IMPORT_TOKEN 环境变量）
6. 端到端测试：本地执行 auto_daily.sh，验证 MySQL 数据更新
7. 配置 launchd 定时任务
8. 观察 3 天自动运行结果，确认稳定

## 10. 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| VPN 未连通导致 pull 失败 | 脚本自动重试 6 次（30 分钟），超时发通知 |
| 导入 API 被恶意调用 | Bearer token 鉴权 + HTTPS 加密传输 |
| 增量数据量超预期 | 请求体 20MB 限制；可按表分批上传 |
| CloudRun 实例冷启动慢 | 保持 1 实例常驻配置不变 |
| 本地 SQLite 与 MySQL schema 不一致 | 导出时只取两库共有的列，忽略差异 |
