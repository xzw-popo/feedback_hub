# CloudBase 部署与优化记录

> 最后更新：2026-06-13

## 一、项目概述

用户反馈智能分发平台（feedback_hub）已成功部署至腾讯云开发（CloudBase），实现前后端分离的云原生架构：

- **前端**：Vue 3 + TypeScript + Element Plus + ECharts → CloudBase 静态托管
- **后端**：Python FastAPI + MySQL → CloudBase CloudRun 容器模式
- **数据库**：CloudBase MySQL (CynosDB)

### 在线地址

| 服务 | URL |
|---|---|
| 前端 Dashboard | https://feedback7-d3gz69ofw321c4da5-1442771950.tcloudbaseapp.com/ |
| 后端 API | https://feedback-api-269678-9-1442771950.sh.run.tcloudbase.com/ |

---

## 二、CloudBase 环境配置

| 配置项 | 值 |
|---|---|
| 环境ID | `feedback7-d3gz69ofw321c4da5`（ap-shanghai） |
| MySQL 地址 | `172.17.0.7:3306`（内网，仅 VPC 内可访问） |
| MySQL Schema | `feedback7-d3gz69ofw321c4da5` |
| MySQL 账号 | `feedback` / `<MySQL 密码>` |
| VPC | `vpc-b58tn9wu` / `subnet-39ye4y0j` |
| CloudRun 服务 | `feedback-api`（version 021，1 CPU / 2GB Mem，最小/最大实例=1） |

### CloudRun 环境变量

```
DB_MODE=mysql
MYSQL_HOST=172.17.0.7
MYSQL_PORT=3306
MYSQL_USER=feedback
MYSQL_PASSWORD=<MySQL 密码>
MYSQL_DATABASE=feedback7-d3gz69ofw321c4da5
CORS_ORIGINS=https://feedback7-d3gz69ofw321c4da5-1442771950.tcloudbaseapp.com
PORT=9000
IMPORT_TOKEN=<导入 API 鉴权密钥，与本地脚本共享>
```

---

## 三、部署架构

```
┌─────────────────────────────────────────────────────────┐
│                     用户浏览器                           │
│         feedback7-d3gz69ofw321c4da5-...tcloudbaseapp.com │
└──────────────────────┬──────────────────────────────────┘
                       │ HTTPS
                       ▼
┌─────────────────────────────────────────────────────────┐
│          CloudBase 静态托管 (Vue3 SPA)                   │
│          SPA 路由 → /index.html 回退                    │
└──────────────────────┬──────────────────────────────────┘
                       │ API 请求
                       ▼
┌─────────────────────────────────────────────────────────┐
│          CloudRun 容器 (feedback-api v021)                │
│          FastAPI + Uvicorn on :9000                     │
│          DB_MODE=mysql → _MySQLConnection (pymysql)     │
│          entrypoint.sh → 仅启动 uvicorn                  │
└──────────────────────┬──────────────────────────────────┘
                       │ 内网 VPC
                       ▼
┌─────────────────────────────────────────────────────────┐
│          CloudBase MySQL (CynosDB)                       │
│          172.17.0.7:3306（仅 VPC 内可访问）              │
│          4 张表: conversation_label / feedback /         │
│                   message_label / push_log              │
└─────────────────────────────────────────────────────────┘
```

### MySQL 外网直连限制

172.17.0.7:3306 是 CloudBase VPC 内网 IP，**无法从外部网络直接连接**。TCP 端口虽可达，但 CloudBase 代理层会返回 HTTP 502 Bad Gateway，不是 MySQL 协议握手。

---

## 四、数据同步机制

### 当前版本（v021+）：每日自动同步流水线

通过 `POST /api/import` 端点实现增量数据同步，无需重建 Docker 镜像。详见 [auto-daily-sync-guide.md](auto-daily-sync-guide.md)。

**数据流**：

```
本地 Mac (pull+tag) → exporter.py 导出 JSON → HTTPS POST /api/import → CloudRun 写 MySQL
```

**关键特性**：

- 时间窗口：绝对日期「昨天一整天」，避免相对时间漂移
- 鉴权：IMPORT_TOKEN 环境变量（Bearer token）
- 幂等：UPSERT 模式，同一天重复导入不产生重复数据
- 自动化：macOS launchd 每天 10:00 触发（需 VPN 连通）
- 手动执行：`bash feedback_hub/auto_daily.sh` 或 `--date YYYY-MM-DD` 补跑

### 旧方式（v017，已弃用但保留）

entrypoint.sh 已简化为仅启动 uvicorn，不再包含任何数据同步逻辑。Docker 镜像中不包含 SQLite 数据库（`.dockerignore` 排除了 `feedback_hub/data/`）。

旧的数据更新流程（需 3 次部署，已不再使用）：

1. 修改 `.dockerignore`：注释掉 `feedback_hub/data/`
2. 修改 `entrypoint.sh`：改为"总是执行 bootstrap_sync"
3. 部署 CloudRun：容器启动时 bootstrap_sync 自动将 SQLite 全量同步到 MySQL
4. 恢复干净部署：`.dockerignore` 恢复排除，重新部署

### bootstrap_sync.py

独立的 SQLite→MySQL 全量同步脚本，仅在有新数据需要同步时临时使用：

- 同步 4 张表：conversation_label、feedback、message_label、push_log
- 使用 `INSERT ... ON DUPLICATE KEY UPDATE`（upsert 模式），幂等安全
- 同步耗时：约 3 分钟（全量 ~200K 行）

---

## 五、部署过程中遇到的问题及解决方案

### 5.1 pymysql API 不兼容 sqlite3

**问题**：`AttributeError: 'Connection' object has no attribute 'execute'`

**原因**：`pymysql.Connection` 没有 `.execute()` 方法，而 `sqlite3.Connection` 有。项目代码中大量使用 `conn.execute(sql, params)` 模式。

**解决方案**：在 `db.py` 中创建 `_MySQLConnection` 包装类，将 `.execute()` 代理为 `conn.cursor().execute()` 并返回 cursor：

```python
class _MySQLConnection:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        cur = self._conn.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self): self._conn.commit()
    def rollback(self): self._conn.rollback()
    def close(self): self._conn.close()
    def cursor(self): return self._conn.cursor()
    def __enter__(self): return self
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None: self._conn.commit()
        else: self._conn.rollback()
        self._conn.close()
        return False
```

### 5.2 CloudBase MySQL 自动添加 `_openid` 列

**问题**：`Field '_openid' doesn't have a default value`

**原因**：CloudBase MySQL 自动为所有表添加 `_openid varchar(64) NOT NULL` 列，INSERT 时不指定该列会导致约束违规。

**解决方案**：对所有相关表执行 ALTER TABLE 修改为 DEFAULT NULL：

```sql
ALTER TABLE conversation_label MODIFY _openid varchar(64) DEFAULT NULL;
ALTER TABLE feedback MODIFY _openid varchar(64) DEFAULT NULL;
ALTER TABLE message_label MODIFY _openid varchar(64) DEFAULT NULL;
ALTER TABLE push_log MODIFY _openid varchar(64) DEFAULT NULL;
ALTER TABLE label_history MODIFY _openid varchar(64) DEFAULT NULL;
```

> **注意**：新建表时需预留此列，或建表后立即 ALTER。

### 5.3 VARCHAR 长度不足

**问题**：`Data too long for column 'enginever' at row N`

**原因**：`enginever` 列定义为 `VARCHAR(64)`，但实际数据中部分开发版本号长达 74 字符；`device_name` 定义为 `VARCHAR(128)`，也有接近上限的数据。

**解决方案**：

```sql
ALTER TABLE feedback MODIFY enginever varchar(128) DEFAULT NULL;
ALTER TABLE feedback MODIFY device_name varchar(255) DEFAULT NULL;
```

同步更新 `schema_mysql.sql` 中的 DDL 定义。

### 5.4 CloudRun 无法访问 MySQL

**问题**：API 返回 500，CloudRun 日志显示连接 MySQL 超时。

**原因**：CloudRun 未配置 VPC，无法通过内网访问 MySQL（172.17.0.7）。

**解决方案**：在 CloudBase 控制台手动配置 VPC：
- 路径：服务设置 → 访问配置 → 编辑 → 开启私有网络 → 选择 `vpc-b58tn9wu` + `subnet-39ye4y0j`

### 5.5 MySQL `rank` 保留字

**问题**：CREATE TABLE push_log 失败。

**原因**：`rank` 是 MySQL 保留字。

**解决方案**：在 `schema_mysql.sql` 中用反引号包裹：`` `rank` ``

### 5.6 SQL 占位符方言差异

**问题**：SQLite 使用 `?` 作为参数占位符，MySQL 使用 `%s`。

**解决方案**：在 `db.py` 中提供 `_ph(n)` 函数动态生成占位符，在 `api.py` 中根据 `DB_MODE` 动态选择占位符，并添加 `_row_val()` 辅助函数兼容 sqlite3.Row 和 pymysql DictCursor 的返回格式差异。

### 5.7 pull 命令超时

**问题**：`python3 -m feedback_hub.cli pull --last 19d` 返回 504 超时。

**原因**：时间窗口过大，单次 API 请求超时。

**解决方案**：改为逐天拉取 `python3 -m feedback_hub.cli pull --date YYYY-MM-DD`。

---

## 六、API 端点清单

| 端点 | 方法 | 说明 | MySQL 模式状态 |
|---|---|---|---|
| `/api/stats/distribution` | GET | L1/L2/severity 分布统计 | ✅ |
| `/api/stats/trend?days=N` | GET | 按天趋势 | ✅ |
| `/api/conversations?page=N` | GET | 会话列表（分页） | ✅ |
| `/api/conversations/{id}` | GET | 会话详情+消息+标签 | ✅ |
| `/api/export.csv?L1=X` | GET | CSV 导出 | ✅ |
| `/api/import` | POST | 批量数据导入（需 IMPORT_TOKEN） | ✅ v021+ |

### MySQL 数据量（截至 2026-06-12）

| 表名 | 行数 |
|---|---|
| conversation_label | 50,357 |
| feedback | 73,996 |
| message_label | 73,996 |
| push_log | 25 |

---

## 七、优化项

### 已完成 ✅

- [x] 全量数据同步到 MySQL（4 张表共 ~200K 行）
- [x] 修复 `_openid` NOT NULL 约束
- [x] 修复 VARCHAR 长度溢出（enginever → 128、device_name → 255）
- [x] `_MySQLConnection` 包装类实现 pymysql/sqlite3 API 兼容
- [x] SQL 占位符方言适配（`?` vs `%s`）
- [x] VPC 内网互联配置
- [x] 移除 Docker 镜像中的 SQLite DB（.dockerignore 排除 `feedback_hub/data/`）
- [x] CORS_ORIGINS 收窄为前端域名（安全加固）
- [x] entrypoint.sh 简化为仅启动 uvicorn（移除 legacy bootstrap_sync 逻辑）
- [x] Dockerfile 移除重复的 pymysql 安装（已在 requirements.txt 中）
- [x] Dockerfile CMD 注释修正（移除过时的 bootstrap_sync 描述）
- [x] 修复 `tagger/pipeline.py` 中硬编码 `?` 占位符（改用 `db._ph()`）
- [x] 统一类型标注：`pusher/push_log.py` 和 `tagger/pipeline.py` 的 `conn` 参数改用 `db.Connection` 类型别名
- [x] 清理历史产物：`sql_batches/`（665 文件, 36.2 MB）、`feedback.db.before_backfill`、`backfill.20260521_1651.log`、`generate_sql_batches.py`
- [x] 数据更新至 2026-06-12（从 49,576 行增长到 73,996 行）
- [x] 每日自动同步流水线：importer.py + exporter.py + auto_daily.sh + launchd
- [x] 新增 `POST /api/import` 端点（Bearer token 鉴权，批量 UPSERT，降级逐条插入）
- [x] CloudRun v021 部署（含 IMPORT_TOKEN 环境变量 + sse-starlette 依赖）
- [x] CORS allow_methods 增加 POST（支持导入 API）

### 未来可做 💡

- [ ] MySQL 凭证通过 Secret Manager 管理（当前明文写在环境变量中）
- [ ] CloudRun 弹性伸缩（当前 MinNum=MaxNum=1）
- [ ] 前端 CDN 加速
- [ ] 监控告警（API 响应时间、错误率）
- [ ] CI/CD 自动化部署流水线

---

## 八、关键文件清单

| 文件 | 说明 |
|---|---|
| `Dockerfile` | CloudRun 容器构建（Python 3.13-slim，DB_MODE=mysql） |
| `entrypoint.sh` | 容器入口：仅启动 uvicorn |
| `.dockerignore` | 排除 data/、tests/、dashboard/、docs/ 等 |
| `feedback_hub/db.py` | 双后端适配层（_MySQLConnection 包装类 + `Connection` 类型别名） |
| `feedback_hub/api.py` | 5 个只读 API 端点 + 1 个写入端点（CORS + 方言适配 + allow_methods=["GET","POST"]） |
| `feedback_hub/importer.py` | 数据导入 API（POST /api/import，Bearer token 鉴权，批量 UPSERT） |
| `feedback_hub/exporter.py` | SQLite → JSON 导出模块（按日期筛选，输出与 import API 一致） |
| `feedback_hub/auto_daily.sh` | 每日自动同步主脚本（VPN 检测 → pull → tag → export → POST → push） |
| `feedback_hub/bootstrap_sync.py` | SQLite→MySQL 全量同步脚本（仅全量初始化场景使用） |
| `feedback_hub/schema_mysql.sql` | MySQL 建表 DDL（VARCHAR 已适配、rank 反引号包裹） |
| `feedback_hub/cli.py` | CLI 入口（支持 PORT/HOST 环境变量） |
| `dashboard/.env.production` | 前端生产环境 API 地址 |
