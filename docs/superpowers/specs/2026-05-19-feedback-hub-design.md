# 用户反馈管理平台 · 数据中枢设计（阶段 1）

> 创建日期：2026-05-19
> 范围：仅"数据中枢 + 打标接入 + 只读 API"。前端、打标提示词迭代、人工 review UI 不在本期范围。

---

## 1. 背景与目标

### 1.1 现状

- ✅ `pull_openapi.py`：从微信输入法 OpenAPI 拉取反馈，写出 `out/{channel}_{tag}.json` + `raw/raw_{channel}_{tag}.json`
- ✅ `tagger/`：规则一审 + LLM 兜底，写出 `_tagged.json / _tagged.csv / _review.csv`
- ✅ `probe_session.py`：探查 OpenAPI 返回结构（2026-05-19 实测结论见 §3.1）
- ⚠️ 数据是**文件**形式，多次拉取会产生大量碎片文件，无法做"按时间区间查询"
- ⚠️ 同一用户反馈对话里的多条消息有不同 `feedback_id`，被当作独立反馈各自打标，导致**统计失真、打标矛盾、LLM 浪费**
- ⚠️ LLM Agent 调用不稳定（已知，本期不解决）

### 1.2 本期目标

把"文件流水线"升级为"**数据中枢 + 文件冷备**"，让后续打标迭代和前端开发都能基于稳定的数据接口独立推进。

具体交付（**全部为新代码，位于 `feedback_hub/` 目录**）：
1. SQLite 数据库 + 4 张表 schema
2. 对话聚合算法（无 session_id，依赖 `user_vid + ts` 启发式推断）
3. 重写的拉取脚本（参考旧 `pull_openapi.py` 协议，但不复用代码）
4. 重写的打标流水线（参考旧 `tagger/` 思路，但不复用代码）
5. 拉取与打标都直接写库（保留 json/csv 冷备）
6. FastAPI 只读查询服务（5 个端点）

### 1.3 非目标（明确排除）

- ❌ LLM 提示词调优、评测集建设（等大模型平台稳定后再做）
- ❌ 前端页面（数据接口稳定后另起一期）
- ❌ 人工 review UI（schema 预留，但本期不实现写接口）
- ❌ PostgreSQL / 多租户 / 消息队列 / ORM 抽象（YAGNI）

### 1.4 实现约束（重要）

**所有代码全部重写，不在现有 `数据采集与打标/` 目录下增量修改、不 import 其中模块、不在运行期调用其中脚本。**

现有产物的定位：

| 现有文件 | 定位 | 本期如何使用 |
|---|---|---|
| `数据采集与打标/pull_openapi.py` | 参考实现 | **只读参考**：协议、字段映射、时间窗口语义 |
| `数据采集与打标/tagger/` | 参考实现 | **只读参考**：规则结构、LLM 客户端协议、JSON 解析容错 |
| `数据采集与打标/l1_l2.md` | 提示词模板 | **直接复用**（数据，不是代码） |
| `数据采集与打标/probe_session.py` | 探查脚本 | **本期已用完，不再调用** |
| `数据采集与打标/raw/` & `out/` | 历史数据 | **不读取**；本期不做"存量数据导入"，从零开始（见 §1.5） |

**含义**：
- 新代码在新目录 `feedback_hub/` 下从零编写
- 不 `import` 旧目录任何模块
- 不通过 `subprocess` 调用旧脚本
- 旧目录文件保持原样（不改、不删）

### 1.5 关于存量数据

由于 §1.4 的约束和"全部重写"的要求，本期**不做存量 `_tagged.json` 导入**。原因：旧打标结果是基于旧规则与旧（不稳定的）LLM 链路产生的，质量参差，导入后反而污染新库。

新库从拉取脚本（重写后的）首次跑通之日开始累积数据。如果将来确实需要导入历史，再单独立项做"存量回灌"，同时重新打标。

§5.3、§7 中相关条目已对齐此原则。

---

## 2. 数据规模与查询模式

| 项 | 取值 | 影响 |
|---|---|---|
| 日增 | 1500~2000 条消息（≈ 800~1200 条对话） | SQLite 完全够，一年 70 万行，查询毫秒级 |
| 历史保留 | 全保留，不归档 | 单库文件预计 1 年 < 500 MB |
| 主查询 | 时间区间 + L1/L2 分布 + 趋势 | 索引优化重点 |
| 写入并发 | 单写（pipeline） | 无锁竞争 |
| 读取并发 | 1~3 并发（少数运营 + 前端） | 无压力 |

---

## 3. 对话聚合算法

### 3.1 OpenAPI 的会话语义（已实测）

通过 `probe_session.py` 实测确认（2026-05-19，1 小时窗口 84 个 session / 278 条 msg）：

- OpenAPI 响应 `results[i]` 是按 **userVid 聚合**的，但 `session` 对象**没有 sessionId**，只有三个字段：`{userVid, channel, serviceVid}`。
- 同 `results[i]` 内 `msg.user.userVid` 与 `session.userVid` **100% 一致**（0/84 不一致）。
- OpenAPI 的"session"语义 ≈ "**同一时间窗内同一 userVid 的所有消息**"，**不区分客服多次接待**。例如用户 14:30 反馈一次、19:30 又反馈一次，落在同一拉取窗口内时会被打包到同一个 `results[i]`。

**结论**：OpenAPI 没给我们直接可用的 session ID，必须自己生成；但天然按 userVid 分桶这件事让我们的算法非常简单。

### 3.2 算法

```python
def assign_conversation_ids(messages, gap_threshold_seconds=1800):
    """
    将消息按 user_vid + 时间相邻性聚合成会话。

    规则：同一 user_vid 内，相邻两条消息时间差 <= 30 分钟视为同一会话；
         超过 30 分钟切成新会话（处理 OpenAPI 不区分多次接待的情况）。
    """
    by_user = group_by(messages, key='user_vid')
    for user_vid, msgs in by_user.items():
        msgs.sort(key=lambda m: m.ts_ms)
        current_conv = None
        prev_ts_ms = None
        seq = 0
        for m in msgs:
            if prev_ts_ms is None or (m.ts_ms - prev_ts_ms) > gap_threshold_seconds * 1000:
                # 开启新会话
                current_conv = sha1(f"{user_vid}:{m.ts_ms}").hexdigest()[:12]
                seq = 0
            m.conversation_id = current_conv
            m.msg_seq = seq
            seq += 1
            prev_ts_ms = m.ts_ms
```

### 3.3 关键决策

- **算法跑在入库时，不跑在拉取时**：拉取脚本 `pull_openapi.py` 不动，保持纯净；会话归属由 `db_writer` 在写库时统一决定。这样跨拉取窗口的会话延续也能正确合并（重算时把同 user_vid 的所有消息一起算）。
- **阈值 30 分钟**：经验值，写在配置里可改。后续观察数据可调。
- **conversation_id 用 `sha1(user_vid:first_ts_ms)[:12]`**：确定性、无需自增、可重算。**幂等保证**：同一组消息无论何时聚合都得到同一 ID。
- **匿名 user_vid**：实测中所有 msg 都有 userVid，但代码留口子——若为空则用 `sha1("anon:" + feedback_id)[:12]`。
- **OpenAPI 的 session 边界仅作参考**：我们不直接使用，因为它只是"同时间窗内同 userVid"的语义，不承诺业务意义上的"一次客服会话"。

### 3.4 已知局限（接受）

- 用户在 30 分钟内连续吐槽两个不同问题 → 会被聚成一个会话。罕见，影响可控（L1 取最严重者，L2 取并集）。
- 不同用户讨论同一个 Bug → 不会合并（按 user_vid 隔离）。预期行为。
- 一个会话横跨两次拉取窗口（如 10:55 一条、11:05 一条，按整点拉取） → 入库时全量重算同 user_vid 的消息，能正确合并。

---

## 4. 数据库 schema

### 4.1 总览

```
feedback (消息原文 + 会话归属)
    │ 1:1
    ├── message_label (每条消息的打标结果，保留诊断用)
    │
    └── 多对一 → conversation_label (会话级聚合标签，前端默认查这张)

label_history (标签变更审计，本期 schema 预留，不写入)
```

### 4.2 表定义

```sql
-- 1. 反馈消息（最细粒度，原样存）
CREATE TABLE feedback (
    feedback_id     TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,         -- 会话 ID，由聚合算法生成
    msg_seq         INTEGER NOT NULL,      -- 会话内序号（0 开始）
    channel         TEXT NOT NULL,         -- 来自 CLI --channel
    ts_ms           INTEGER NOT NULL,      -- unix 毫秒（OpenAPI 原生单位）
    platform        TEXT,
    appversion      TEXT,
    user_vid        TEXT,
    keyboard_source TEXT,
    device_name     TEXT,
    channelid       TEXT,
    enginever       TEXT,
    msgtype         TEXT,                  -- 当前固定 'text'，留口子给 image/voice
    text            TEXT NOT NULL,
    tags            TEXT,                  -- OpenAPI 自带标签，'|' 分隔
    raw_json        TEXT,                  -- 其它字段（device、url、scheme、replyId 等）透传
    pulled_at       INTEGER NOT NULL       -- 入库时间（秒）
);
CREATE INDEX idx_feedback_conv ON feedback(conversation_id, msg_seq);
CREATE INDEX idx_feedback_user_ts ON feedback(user_vid, ts_ms);
CREATE INDEX idx_feedback_ts ON feedback(ts_ms);

-- 2. 消息级标签（保留每条消息的原始打标）
CREATE TABLE message_label (
    feedback_id     TEXT PRIMARY KEY,
    L1              TEXT NOT NULL,
    L2              TEXT,                  -- '|' 分隔
    severity        TEXT NOT NULL,
    confidence      REAL NOT NULL,
    reason          TEXT,
    source          TEXT NOT NULL,         -- rule / llm
    rule_name       TEXT,
    tagged_at       INTEGER NOT NULL,
    FOREIGN KEY (feedback_id) REFERENCES feedback(feedback_id)
);

-- 3. 会话级聚合标签（前端默认查这张）
CREATE TABLE conversation_label (
    conversation_id TEXT PRIMARY KEY,
    L1              TEXT NOT NULL,         -- 取最严重的
    L2              TEXT,                  -- 所有消息的并集
    severity        TEXT NOT NULL,
    confidence      REAL NOT NULL,         -- 取被选中那条消息的 confidence
    reason          TEXT,
    source          TEXT NOT NULL,         -- 'aggregated'
    msg_count       INTEGER NOT NULL,
    first_ts_ms     INTEGER NOT NULL,
    last_ts_ms      INTEGER NOT NULL,
    user_vid        TEXT,
    appversion      TEXT,                  -- 取该会话最早一条
    channel         TEXT NOT NULL,
    aggregated_at   INTEGER NOT NULL
);
CREATE INDEX idx_clabel_l1_ts ON conversation_label(L1, last_ts_ms);
CREATE INDEX idx_clabel_ts ON conversation_label(last_ts_ms);
CREATE INDEX idx_clabel_severity ON conversation_label(severity, last_ts_ms);

-- 4. 标签变更历史（schema 预留，本期不写入）
CREATE TABLE label_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type     TEXT NOT NULL,         -- 'conversation' / 'message'
    target_id       TEXT NOT NULL,
    L1              TEXT,
    L2              TEXT,
    severity        TEXT,
    confidence      REAL,
    reason          TEXT,
    source          TEXT,
    operator        TEXT,
    created_at      INTEGER NOT NULL
);
CREATE INDEX idx_history_target ON label_history(target_type, target_id);
```

### 4.3 聚合规则（写入 `conversation_label` 时执行）

| 字段 | 规则 |
|---|---|
| `L1` | 取该会话所有 `message_label.L1` 中**最严重的**：`A.Bug > B.建议 > C.咨询 > D.情绪 > E.无效 > 待定` |
| `L2` | 所有消息的 L2 取并集 |
| `severity` | 取最严重的：`P0 > P1 > P2 > P3` |
| `confidence` | 取被选中那条 L1 消息的 confidence |
| `reason` | 取被选中那条消息的 reason；如果有多条同 L1，挑 confidence 最高那条 |
| `source` | 固定 `'aggregated'` |
| `msg_count / first_ts_ms / last_ts_ms` | SQL 聚合 |
| `user_vid / appversion / channel` | 取会话第一条消息（msg_seq=0）的值 |

---

## 5. 数据写入流程

> **本期所有脚本与模块均在新目录 `feedback_hub/` 下从零编写，不复用旧 `数据采集与打标/` 目录的代码。** 旧代码仅作为参考实现来对照协议、字段、规则结构。

### 5.1 拉取（重写）

新建 `feedback_hub/puller.py`，参考旧 `pull_openapi.py` 的协议（端点、headers、body、二次时间过滤），但代码全部新写。**职责单一：拉数据 → 直接写库 + 写一份冷备 JSON 到 `feedback_hub/data/raw/`**。

差异点（与旧实现相比）：
- **直接写库**：拉到的 msg 直接 upsert 进 `feedback` 表，不再依赖中间 json
- **冷备 JSON 仅作 debug 用**：保留 OpenAPI 原始响应到 `data/raw/`，便于排查
- **不算 conversation_id**：会话归属由打标流水线在写库后统一计算（见 §5.2 步骤 2）
- **不写 csv**：csv 导出由 API 层 `/api/export.csv` 统一负责

### 5.2 打标 + 入库（重写）

新建 `feedback_hub/tagger/`（结构参考旧 `tagger/`，但代码全部新写）。流水线：

```
db.feedback (新拉的 + 历史) → 取出未打标的 → tag_rows(规则+LLM) →
  → upsert message_label
  → 重新计算受影响 user_vid 的 conversation_id
  → 重算受影响的 conversation_label
```

详细步骤：

1. **取待打标消息**：`SELECT feedback_id, text FROM feedback WHERE feedback_id NOT IN (SELECT feedback_id FROM message_label)`
2. **打标**：规则一审（参考旧 `rules.yaml`，但 yaml 文件本期可直接拷贝过来作为初始数据）+ LLM 兜底
3. **upsert message_label**：按 `feedback_id` 写
4. **会话聚合（仅在新增/变更涉及的 user_vid 范围内）**：
   - 取出本批次涉及的所有 `user_vid`
   - 对每个 user_vid，从 `feedback` 表读出该用户所有消息（含历史），按 §3.2 算法重新分配 `conversation_id` 与 `msg_seq`
   - update 回 `feedback` 表
5. **重算 conversation_label**：对本批次涉及的所有 `conversation_id`，从 `message_label` 重新聚合（不是增量，是全量重算这些会话）

### 5.3 不做存量数据导入

按 §1.5：本期不导入旧 `_tagged.json`，新库从零累积。

### 5.4 幂等性保证

- 重跑拉取（同一时间窗）→ feedback_id 主键冲突时跳过，不产生重复行
- 重跑打标 → message_label 主键 upsert，覆盖旧标签
- 跨批次的会话延续 → 聚合时按 user_vid 全量重算，所以不会"裂开"

---

## 6. FastAPI 只读服务

### 6.1 端点列表

| 端点 | 用途 | 主要参数 |
|---|---|---|
| `GET /api/conversations` | 会话列表（前端默认） | `from, to, L1, L2, severity, q, limit, offset` |
| `GET /api/conversations/{conv_id}` | 单个会话详情（含所有消息） | - |
| `GET /api/stats/distribution` | L1/L2/severity 分布 | `from, to` |
| `GET /api/stats/trend` | 按天/小时趋势 | `from, to, granularity` |
| `GET /api/export.csv` | CSV 导出 | 同 `/api/conversations` |

### 6.2 数据契约示例

```json
// GET /api/conversations?from=2026-05-18&to=2026-05-19&L1=A.Bug&limit=20
{
  "total": 142,
  "items": [
    {
      "conversation_id": "a3f1c8e9d2b4",
      "L1": "A.Bug",
      "L2": ["输入核心", "性能"],
      "severity": "P0",
      "confidence": 0.9,
      "reason": "P0 关键词 闪退",
      "msg_count": 3,
      "first_ts_ms": 1747526400000,
      "last_ts_ms": 1747527120000,
      "user_vid": "xxx",
      "appversion": "1.2.3",
      "preview_text": "刚才打字直接闪退了..."
    }
  ]
}
```

### 6.3 实现约束

- 单文件 `app.py` 起步，~200 行内
- 直接用 sqlite3 标准库，不引 ORM
- 无鉴权（内网工具）
- 不实现 POST/PATCH 写接口（schema 预留 `label_history`，等下一期人工 review 再做）

---

## 7. 项目结构

```
用户反馈_2026_0519/
├── 数据采集与打标/                        # 旧目录，本期不修改、不调用、不 import
│   ├── pull_openapi.py                  # 仅作协议参考
│   ├── tagger/                          # 仅作规则与 LLM 解析参考
│   ├── l1_l2.md                         # 提示词（数据，可直接复制使用）
│   ├── probe_session.py                 # 已用完
│   ├── out/  raw/                       # 历史数据，本期不读取
│   └── ...
│
├── feedback_hub/                        # 本期所有新代码（全部新写）
│   ├── __init__.py
│   ├── config.py                        # 常量：DB 路径、阈值、agent_key、URL 等
│   ├── schema.sql                       # 4 张表 DDL
│   ├── db.py                            # SQLite 连接 + upsert helpers
│   ├── conversation.py                  # 会话聚合算法（§3.2）
│   ├── puller.py                        # 拉取脚本（重写，参考 pull_openapi.py）
│   ├── tagger/                          # 打标流水线（重写，参考旧 tagger/）
│   │   ├── __init__.py
│   │   ├── rules.py                     # 规则一审
│   │   ├── rules.yaml                   # 规则数据（可从旧目录拷贝作为初始值）
│   │   ├── llm_client.py                # LLM 客户端
│   │   ├── label_parser.py              # JSON 解析容错
│   │   ├── prompt.md                    # 从 l1_l2.md 拷贝
│   │   └── pipeline.py                  # 编排：取待打标 → 规则 → LLM → 写库
│   ├── api.py                           # FastAPI 应用
│   ├── cli.py                           # 统一入口：python -m feedback_hub.cli pull|tag|serve
│   ├── data/                            # gitignore
│   │   ├── feedback.db                  # SQLite 文件
│   │   └── raw/                         # 拉取冷备 JSON
│   └── tests/
│       ├── test_conversation.py
│       ├── test_db.py
│       └── test_aggregation.py
│
└── docs/superpowers/specs/
    └── 2026-05-19-feedback-hub-design.md   # 本文件
```

**关键纪律**：
- `feedback_hub/` 任何文件 **不得 import** `数据采集与打标/` 下的模块
- `feedback_hub/` 任何脚本 **不得通过 subprocess** 调用 `数据采集与打标/` 下的脚本
- 唯一允许的复用方式：**复制粘贴** `rules.yaml` 与 `l1_l2.md` 内容（数据，不是代码）作为初始值

---

## 8. 实施阶段（高层）

> 详细任务拆分由后续 writing-plans 阶段产出。

| 阶段 | 内容 | 预计 |
|---|---|---|
| P1 | `feedback_hub/` 骨架：DB schema + conversation.py + db.py + 单元测试 | 1 天 |
| P2 | 重写 puller.py（参考旧 pull_openapi.py），打通"拉取 → 写 feedback 表" | 0.5 天 |
| P3 | 重写 tagger/，打通"取待打标 → 规则+LLM → 写 message_label → 重算会话标签" | 1 天 |
| P4 | FastAPI 5 个端点 + 手工验证 | 1 天 |
| P5 | 自检：连续跑 3 天 cron，对比"消息级 vs 会话级"反馈数量比例，验证去重效果 | 0.5 天 |

---

## 9. 风险与已知问题

| 风险 | 影响 | 缓解 |
|---|---|---|
| 30 分钟阈值不准 | 对话被切碎或粘连 | 写成配置；阶段 P5 用真实数据校验切分分布 |
| 拉取间隙导致会话断开 | 一个会话被分两次入库，第二次需重算合并 | §5.2 步骤 2 的"全量重算同 user_vid"已覆盖 |
| `feedback_id` 仅用户消息有 | 客服回复消息（sender=1）没有 feedback_id | 重写的 puller.py 同样过滤 sender≠0，只入库用户消息 |
| OpenAPI 不返回 sessionId | 必须自算会话 ID | 已通过 §3.2 算法解决；conversation_id 由 sha1 生成且幂等可重算 |
| `user_vid` 缺失 | 实测中所有 msg 都有 userVid，但理论上可能为空 | 代码留口子，匿名时退化为 `sha1("anon:" + feedback_id)[:12]`，每条独立成会话 |
| LLM 不稳导致大量"待定" | 影响 conversation_label 的 L1 准确性 | 本期接受，等打标平台稳定后改进 |

---

## 10. 验收标准

- [ ] `feedback_hub/data/feedback.db` 能被 sqlite3 CLI 打开，4 张表存在
- [ ] 跑 `python -m feedback_hub.cli pull --last 1h` → `feedback` 表能查到对应记录
- [ ] 跑 `python -m feedback_hub.cli tag` → `message_label / conversation_label` 表有对应记录
- [ ] 同一时间窗的拉取连跑两次，feedback 行数不增加（幂等）
- [ ] 同一批未变化的消息打标连跑两次，message_label 行数不增加（幂等）
- [ ] FastAPI 5 个端点本地能调通，返回结构如 §6.2
- [ ] 连续跑 3 天 cron 后，输出"消息级 vs 会话级"反馈数量比例报告（验证去重效果）
- [ ] 全程没有 import 或调用旧 `数据采集与打标/` 目录下的代码（grep 验证）
