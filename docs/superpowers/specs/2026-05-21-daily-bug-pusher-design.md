# 每日 Top Bug 推送 · 设计 spec（阶段 2）

> 创建日期：2026-05-21
> 范围：从 `feedback_hub` 现有数据中枢出发，新增"每日 Top 5 Bug 自动推送到企微群"的能力。
> 前置依赖：阶段 1 spec（`2026-05-19-feedback-hub-design.md`）已实现，`conversation_label` 表有数据。
> 产品定位：MVP，单向推送、不收反馈，先证明价值再要求开发投入打标。

---

## 1. 背景与目标

### 1.1 现状

- ✅ `feedback_hub/` 数据中枢已运行：`feedback / message_label / conversation_label` 三表持续积累数据
- ✅ 打标产出字段：`L1 / L2 / severity(P0~P3) / confidence(0~1) / source(rule|llm)`
- ✅ FastAPI 只读服务能查会话列表、分布、趋势
- ⚠️ **打标质量无量化反馈**：开发没机会看到打标结果，无从评价
- ⚠️ **运营每天人工捞数据**：没有"主动推送 Top 问题"的链路
- ⚠️ **没有"重复问题聚合"**：同一个 Bug 被 N 个用户反馈，目前是 N 条独立 conversation，热度信号不可见

### 1.2 本期目标

把"被动查询"升级为"**主动推送 Top 5**"：每天 12:00 向企微群推送过去 7 天聚合后排名前 5 的 Bug 候选，每条带"为什么是 Top 5"的解释（重复次数、影响版本）。

具体交付：
1. **Bug 关键词分组**：8 组具体 + 1 组兜底，落到 `feedback_hub/pusher/kw_groups.yaml`
2. **签名计算 + 聚合打分**：纯 Python 模块，运行时实时算（不入库）
3. **Top 5 候选生成器**：从 `conversation_label` 查 7 天窗口数据 → 算签名 → 打分排序 → 取 Top 5
4. **企微推送器**：webhook 推送，Markdown 卡片格式
5. **`push_log` 表**：记录每日推送内容，作为未来 A→B（加反馈采集）的钩子
6. **30 天历史回灌脚本**：按天循环，复用现有 `puller.py` + `tagger/`
7. **CLI 入口**：`python -m feedback_hub.cli backfill / push`

### 1.3 非目标（明确排除）

- ❌ 收集开发反馈（is_real_bug、wow/meh）—— A→B 升级，下一期再做
- ❌ Dashboard 展示推送历史 —— 复用现有 dashboard，本期不动前端
- ❌ 自动 cron —— MVP 头两周手动跑，验证质量后再上 launchd / crontab
- ❌ 多群 / 多机器人配置 —— 单 webhook 起步
- ❌ 把 signature 物化到 `conversation_label` 表 —— 分组规则会迭代，回填成本大于收益
- ❌ 文本相似度 / embedding 聚类 —— 关键词归并已够用

### 1.4 决策依据汇总（brainstorm 结论）

| 主题 | 决策 | 依据 |
|---|---|---|
| 推送形态 | A 方案：单向推送，不收反馈 | demo 阶段先证明价值，再要求开发投入 |
| 每日规模 | Top 5 | 5 条精品 >> 20 条平庸；群消息体感不刷屏 |
| 筛选条件 | `L1=A.Bug AND severity∈{P0,P1} AND confidence≥0.7` | 在现有打标字段下唯一可行的"严重度阈值" |
| 排序依据 | 聚合签名打分（重复数 + P0 数 + 跨版本 + 新鲜度） | P0/P1 是离散桶，桶内必须靠"重复 + 跨版本"拉开差距 |
| 签名定义 | `(group_id, primary_l2, major_version)` | 同 group + 同 version 下，不同 L2 是不同问题 |
| 多组命中 | 具体组优先，generic_bug 让位 | 解决"先说有 bug，后说细节"的边界 case |
| 仅命中 generic_bug | 不参与聚合也不进 Top 5 | 同签名下五花八门的真实问题，聚合失真 |
| 重复窗口 | 过去 7 天 | 平衡"近期热点"与"统计显著性" |
| 历史回灌 | 30 天，按天循环 | 让首日推送就有 7 天可用窗口 |
| 采集时机 | 每天 09:00 拉前一天自然日 | 留 3h 缓冲给重跑；OpenAPI 延迟 ~10min，09:00 安全 |
| 推送时机 | 每天 12:00 | 午休前 / 工作过半，开发最容易点开看 |
| 中间 review | 11:55 人工扫一眼（MVP 头两周） | 早期防 LLM 抽风推水货，毁信任 |

---

## 2. Bug 关键词分组（最终定稿）

### 2.1 分组表

| 组 ID | 含义 | 关键词 | 默认严重度倾向 |
|---|---|---|---|
| `crash` | 闪退/崩溃/启动失败 | 闪退、崩溃、crash、打不开、无法启动、启动不了、白屏、黑屏、自动退出、重启 | P0 |
| `input_dead` | 输入功能失活 | 无法输入、输入不了、打不出字、敲不了字、失灵、失效 | P0 |
| `unusable` | 用户主观陈述"不能用" | 用不了、无法使用、完全不能用、完全用不了 | P0 |
| `lag` | 卡顿/延迟 | 卡死、卡住、卡顿、反应慢、延迟 | P1 |
| `power` | 发热/耗电 | 发烫、耗电、掉电 | P1 |
| `garbled` | 内容错乱 | 乱码、错位、错乱、丢字、漏字、显示不全 | P1 |
| `missing` | 元素消失 | 不显示、看不到、消失了、找不到 | P2 |
| `generic_bug` | 泛词兜底 | bug、BUG、故障、异常 | — |

### 2.2 多组命中规则

```python
SPECIFIC_GROUPS = {"crash", "input_dead", "unusable", "lag", "power", "garbled", "missing"}
PRIORITY = ["crash", "input_dead", "unusable", "garbled", "lag", "power", "missing"]

def assign_group(text: str) -> str | None:
    hits = [g for g, kws in KW_GROUPS.items() if any(kw in text for kw in kws)]
    # 规则 1：同时命中泛词组与具体组 → 丢弃泛词组
    if "generic_bug" in hits and any(g in SPECIFIC_GROUPS for g in hits):
        hits.remove("generic_bug")
    # 规则 2：多个具体组都命中 → 按优先级取一个
    for g in PRIORITY:
        if g in hits:
            return g
    if hits == ["generic_bug"]:
        return "generic_bug"
    return None
```

### 2.3 签名定义

签名定义见 §2.5（方案 X：基于全会话拼接文本计算）。这里只声明结构：

```python
signature = (group_id, primary_l2, major_version)
# 例: ("crash", "输入核心", "5.4")
```

`generic_bug` 单独命中的会话：返回 `None`，**不参与聚合也不进 Top 5 候选**。

### 2.4 配置外置

分组表落到 `feedback_hub/pusher/kw_groups.yaml`：
```yaml
groups:
  crash:
    keywords: [闪退, 崩溃, crash, 打不开, 无法启动, 启动不了, 白屏, 黑屏, 自动退出, 重启]
  input_dead:
    keywords: [无法输入, 输入不了, 打不出字, 敲不了字, 失灵, 失效]
  # ...
specific_groups: [crash, input_dead, unusable, lag, power, garbled, missing]
priority_order: [crash, input_dead, unusable, garbled, lag, power, missing]
scoring:
  w_dup_count: 3.0
  w_p0_count: 2.0
  w_cross_version: 5.0
  w_recent_24h: 2.0
```
调整分组无需改代码，重启即可。

### 2.5 签名计算的文本来源（方案 X：拼接全会话）

**问题背景**：`conversation_label` 表只保留聚合后的 L1/L2/severity 与一条 reason，**不存原始消息文本**。签名匹配必须 join 回 `feedback` 表拿原文。这里有三种取法：

- ❌ **取首条**（msg_seq=0）：易漏召。用户常常先寒暄/报版本/说"有个 bug"再描述症状，首条不含关键词→签名为 None→整条会话被排除。
- ❌ **取 reason**：reason 是 LLM/规则产出的解释，关键词可能已被改写吃掉。
- ✅ **取整条会话所有用户消息文本拼接**（采用）：召回率最高，泛词+具体词都在拼接文本里，§2.2 的"具体组让位"规则自动生效。

**实现**：

```python
# SQL 端用 GROUP_CONCAT 拼接（按 msg_seq 升序），分隔符用 ' || '
SELECT GROUP_CONCAT(text, ' || ') 
FROM (SELECT text FROM feedback WHERE conversation_id=? ORDER BY msg_seq) 
```

```python
def compute_signature(conv) -> tuple[str, str, str] | None:
    # full_text = 该会话所有 feedback.text 按 msg_seq 升序拼接
    group = assign_group(conv.full_text)
    if group is None or group == "generic_bug":
        return None
    primary_l2 = (conv.L2.split("|")[0] if conv.L2 else "_unknown")
    major_ver = (conv.appversion.rsplit(".", 1)[0] if conv.appversion else "_unknown")
    return (group, primary_l2, major_ver)
```

**注意区分两种文本用途**：

| 用途 | 文本来源 | 理由 |
|---|---|---|
| **签名匹配** | 全会话拼接 `full_text` | 召回率最大化 |
| **群里展示的代表文本**（§4.1 引用块） | 该会话内 confidence 最高的那条 message_label 对应的 `feedback.text` | 一句话最能"代表"这个会话的判定依据；避免群里展示出"你好"这种首条寒暄 |

**性能**：7 天 P0/P1 Bug 会话量级估计 200~1500 条，GROUP_CONCAT + Python 端正则匹配毫秒级，无压力。

**已知局限**（接受）：
- 拼接文本可能命中多个具体组关键词（罕见但存在）→ 由 §2.2 优先级规则取一个，其余信息丢失。这正是我们想要的行为。
- 同会话不同消息分别命中"crash"和"missing"等不同组 → 该会话只进 crash 桶（按优先级）；missing 信号丢失。**这是设计取舍**：会话级聚合的"用户内去重"价值大于"多签名召回"。

---

## 3. 推送候选生成

### 3.1 数据来源

候选**只从 `conversation_label` 表查**，不直接查 `feedback`。原因（来自阶段 1）：会话级聚合已完成"用户内去重"，避免一个用户连发 5 条骂同一个 bug 被算成 5 票。

### 3.2 SQL 拉取

候选 SQL 一次性拉出三种文本：`full_text`（签名匹配用）、`top_conf_text`（展示用）、`top_conf_feedback_id`（审计用）。

```sql
WITH bug_convs AS (
    SELECT cl.conversation_id, cl.L1, cl.L2, cl.severity, cl.confidence,
           cl.reason, cl.msg_count, cl.first_ts_ms, cl.last_ts_ms,
           cl.user_vid, cl.appversion, cl.channel
    FROM conversation_label cl
    WHERE cl.L1 = 'A.Bug'
      AND cl.severity IN ('P0', 'P1')
      AND cl.confidence >= 0.7
      AND cl.last_ts_ms >= :seven_days_ago_ms
),
-- 全会话拼接文本（按 msg_seq 升序）：用于签名匹配
full_text_per_conv AS (
    SELECT f.conversation_id,
           GROUP_CONCAT(f.text, ' || ') AS full_text
    FROM (SELECT conversation_id, text FROM feedback
          WHERE conversation_id IN (SELECT conversation_id FROM bug_convs)
          ORDER BY conversation_id, msg_seq) f
    GROUP BY f.conversation_id
),
-- 同 conv 内 confidence 最高的那条 message_label：用于代表文本
top_conf_msg AS (
    SELECT ml.feedback_id, f.conversation_id, f.text AS top_conf_text, ml.confidence,
           ROW_NUMBER() OVER (
               PARTITION BY f.conversation_id
               ORDER BY ml.confidence DESC, f.msg_seq ASC
           ) AS rn
    FROM message_label ml
    JOIN feedback f ON f.feedback_id = ml.feedback_id
    WHERE f.conversation_id IN (SELECT conversation_id FROM bug_convs)
)
SELECT bc.*, ft.full_text,
       tc.feedback_id AS top_conf_feedback_id, tc.top_conf_text
FROM bug_convs bc
LEFT JOIN full_text_per_conv ft ON ft.conversation_id = bc.conversation_id
LEFT JOIN top_conf_msg tc ON tc.conversation_id = bc.conversation_id AND tc.rn = 1
ORDER BY bc.last_ts_ms DESC;
```

兜底：极端情况下 `top_conf_text` 为 NULL（理论不会，message_label 必有同 conv 的至少一条），代码层 fallback 到 `full_text` 截断到 100 字。

### 3.3 聚合打分

```python
@dataclass
class ConversationRow:
    conversation_id: str
    L1: str; L2: str; severity: str; confidence: float
    appversion: str | None
    last_ts_ms: int
    full_text: str               # 签名匹配用（全会话拼接）
    top_conf_text: str           # 展示用（confidence 最高消息原文）
    top_conf_feedback_id: str

@dataclass
class CandidateGroup:
    signature: tuple[str, str, str]
    conversations: list[ConversationRow]
    score: float = 0.0
    representative_text: str = ""           # 群里展示用
    representative_conv_id: str = ""
    representative_feedback_id: str = ""    # 落 push_log 便于审计

def aggregate_and_score(rows, now_ms, weights) -> list[CandidateGroup]:
    buckets = defaultdict(list)
    for r in rows:
        sig = compute_signature_from_full_text(r.full_text, r.L2, r.appversion)
        if sig is None:
            continue
        buckets[sig].append(r)
    
    groups = []
    for sig, convs in buckets.items():
        dup_count = len(convs)
        p0_count = sum(1 for c in convs if c.severity == "P0")
        versions = {c.appversion for c in convs if c.appversion}
        cross_version = 1 if len(versions) > 1 else 0
        recent_24h = 1 if any(c.last_ts_ms >= now_ms - 86400_000 for c in convs) else 0
        
        score = (
            dup_count     * weights["w_dup_count"]
          + p0_count      * weights["w_p0_count"]
          + cross_version * weights["w_cross_version"]
          + recent_24h    * weights["w_recent_24h"]
        )
        # 代表会话：bucket 内 confidence 最高的那个 conversation
        rep = max(convs, key=lambda c: c.confidence)
        groups.append(CandidateGroup(
            signature=sig, conversations=convs, score=score,
            representative_text=rep.top_conf_text or rep.full_text[:100],
            representative_conv_id=rep.conversation_id,
            representative_feedback_id=rep.top_conf_feedback_id,
        ))
    
    return sorted(groups, key=lambda g: g.score, reverse=True)
```

**关键变化（vs spec 上一版）**：
- `compute_signature` 接受 `full_text`，不再是单条 text
- `CandidateGroup` 区分 `representative_text`（展示）和 `full_text`（已被消费在签名计算中）
- `representative_feedback_id` 落到 `push_log`（见 §5.1 schema 同步更新）

### 3.4 权重默认值

| 维度 | 权重 | 说明 |
|---|---|---|
| `dup_count`（重复会话数） | 3.0 | 影响面是核心 |
| `p0_count`（P0 子项数） | 2.0 | 在重复基础上加成 |
| `cross_version`（跨版本） | 5.0 | "不是个例"的强信号，加成最重 |
| `recent_24h`（24h 内出现） | 2.0 | 防老问题霸榜 |

权重落到 `kw_groups.yaml` 的 `scoring:` 节，方便观察一周后调。

### 3.5 Top 5 选取

```python
def pick_top5(groups: list[CandidateGroup]) -> list[CandidateGroup]:
    return groups[:5]
```

**没有阈值兜底**——只要满足 `severity∈{P0,P1} + confidence≥0.7` 就算够格。如果某天 0 个候选，推送一句"今日无 Top Bug，已扫描 N 条会话"，避免群里完全没动静让人怀疑机器人挂了。

---

## 4. 企微推送

### 4.1 消息格式（Markdown）

```
## 📊 今日 Top Bug 候选 · 2026-05-22

> 数据窗口：过去 7 天 · 扫描 142 个 Bug 会话 · 命中 28 个签名

---

**1. [P0] 闪退类 · 输入核心 · 5.4 版本** · 重复 12 次
> "微信里打字突然闪退，重启也没用"
> 涉及版本：5.4.0 (3), 5.4.1 (9)

**2. [P0] 输入失效 · 跨版本** · 重复 7 次
> "今天突然打不出字，按键没反应"
> 涉及版本：5.3.8 (2), 5.4.1 (5)

...

---
🤖 by feedback_hub · 反馈或建议请联系 @charvel
```

字段含义：
- `[P0]`：取 bucket 内最严重的 severity
- `闪退类`：group_id 的中文映射（`crash → 闪退类` 等，落 `kw_groups.yaml` 的 `display_names:` 节）
- `输入核心`：primary_l2
- `5.4 版本` / `跨版本`：当 `cross_version=0` 显示具体版本号，=1 显示"跨版本"
- `重复 12 次`：dup_count
- **引用块文本**：`representative_text`——bucket 内 confidence 最高那个会话的、该会话内 confidence 最高那条消息的原文（见 §3.3）。**不是首条，避免出现"你好"这种寒暄被推到群里**
- `涉及版本`：按版本分组计数，按数量降序

### 4.2 webhook 配置

- 配置项放 `feedback_hub/config.py` 的 `WECHAT_WEBHOOK_URL`，用环境变量 `WECHAT_WEBHOOK_URL` 覆盖
- 失败重试：3 次，指数退避（1s / 2s / 4s）
- dry-run：`python -m feedback_hub.cli push --dry-run` 只打印消息体不发送，便于人工 review

### 4.3 边界

- 候选为 0 → 推一句"今日无 Top Bug"，**仍然落 push_log**（标记 `is_empty=1`）
- webhook 三次失败 → 写错误日志到 stderr，**push_log 仍落库**但 `delivered_at=NULL`，便于事后人工补发

---

## 5. push_log 表

### 5.1 表定义

```sql
CREATE TABLE push_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    push_date       TEXT NOT NULL,                       -- 'YYYY-MM-DD'
    rank            INTEGER NOT NULL,                    -- 1~5；is_empty=1 时为 0 占位
    signature       TEXT,                                -- 'group|primary_l2|major_version'
    group_id        TEXT,
    primary_l2      TEXT,
    major_version   TEXT,
    representative_conversation_id TEXT,                 -- 代表会话
    representative_feedback_id     TEXT,                 -- 代表消息（confidence 最高那条）
    score           REAL,
    dup_count       INTEGER,
    p0_count        INTEGER,
    cross_version   INTEGER,                             -- 0/1
    affected_versions TEXT,                              -- '5.4.0:3|5.4.1:9' 形式
    representative_text TEXT,                            -- 群里展示的那段引用原文
    created_at      INTEGER NOT NULL,                    -- 候选生成时间
    delivered_at    INTEGER,                             -- 推送成功时间，NULL=未送达
    is_empty        INTEGER NOT NULL DEFAULT 0           -- 1=当日 0 候选的占位行
);
CREATE INDEX idx_pushlog_date ON push_log(push_date);
CREATE INDEX idx_pushlog_sig ON push_log(signature);
CREATE INDEX idx_pushlog_repconv ON push_log(representative_conversation_id);
CREATE INDEX idx_pushlog_repmsg ON push_log(representative_feedback_id);
```

### 5.2 用途

1. **审计**：今天到底推了什么 / 有没有推
2. **未来 A→B 升级钩子**：将来要给开发发 review 链接，URL 用 `push_log.id` 作标识
3. **效果分析**：对比"曾被推过的会话"和"未被推过的会话"是否被开发更早跟进（手工对账）
4. **去重防霸榜**：v1 不做；v2 可加 `WHERE signature NOT IN (近 N 天 push_log)`

### 5.3 schema 注册

新增到 `feedback_hub/schema.sql`，与现有 4 张表并列。`db.py` 里的 `init_db` 确保该表存在（`CREATE TABLE IF NOT EXISTS`，向后兼容）。

---

## 6. 30 天历史回灌

### 6.1 实现方式

新增 `feedback_hub/scripts/backfill.sh`，按天循环 30 次：

```bash
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

for i in $(seq 30 -1 1); do
  DATE_START=$(date -v-${i}d +%Y-%m-%d)
  DATE_END=$(date -v-$((i-1))d +%Y-%m-%d)
  echo ">>> [$(date +%H:%M:%S)] 回灌 ${DATE_START} 00:00 ~ ${DATE_END} 00:00"
  python -m feedback_hub.cli pull \
    --start "${DATE_START} 00:00:00" \
    --end "${DATE_END} 00:00:00" \
    || { echo "!!! pull 失败 ${DATE_START}，跳过"; continue; }
done

echo ">>> 拉取完成，开始全量打标..."
python -m feedback_hub.cli tag

echo ">>> 回灌完成"
```

### 6.2 关键设计点

- **拉取按天循环、打标一次性跑**：拉取按天主要是控制响应大小 + 失败可单天重跑；打标 pipeline 内部会自取"未打标的全量"，没必要按天切分
- **失败容错**：单天 pull 失败 `continue`，不阻塞整体；最后人工补
- **幂等**：依赖阶段 1 已实现的 `INSERT OR IGNORE`（feedback 主键）和 message_label upsert，重跑无害
- **耗时估算**：30 天预计 30 × 10s（拉）+ 30~60min（打标）≈ 1 小时内
- **macOS 与 Linux**：`date -v-${i}d` 是 BSD 写法，仅支持 macOS。在 Linux 环境下要用 `date -d "$i days ago"`，本期只在 macOS 跑就够（charvel 本机），未来上服务器再适配。

### 6.3 执行时机

- **回灌只跑一次**，在首次部署完日常流水线之后立即执行
- 回灌后立刻可以进行第一次 `push --dry-run` 看 Top 5 形态

---

## 7. 项目结构

```
feedback_hub/
├── ... (阶段 1 已有)
├── schema.sql                          # 追加 push_log 表 DDL
├── pusher/                             # 本期新增
│   ├── __init__.py
│   ├── kw_groups.yaml                  # 分组 + 优先级 + 权重 + 显示名
│   ├── signature.py                    # assign_group / compute_signature
│   ├── scorer.py                       # aggregate_and_score / pick_top5
│   ├── candidate.py                    # SQL 拉取 + 编排（候选生成主入口）
│   ├── formatter.py                    # 候选 → Markdown 消息体
│   ├── webhook.py                      # 企微 webhook 客户端（重试）
│   └── push_log.py                     # push_log 表读写
├── scripts/
│   └── backfill.sh                     # 30 天回灌脚本
├── cli.py                              # 追加 backfill / push 子命令
└── tests/
    ├── test_signature.py               # 多组命中、generic_bug 让位、版本切分
    ├── test_scorer.py                  # 评分公式、Top 5 选取、空候选
    ├── test_formatter.py               # Markdown 格式快照测试
    └── test_push_log.py                # 落库 + delivered_at 更新
```

**纪律**：
- `pusher/` 不写库以外的状态（无全局变量）
- `kw_groups.yaml` 是唯一的"业务参数"来源，代码里禁止硬编码关键词

---

## 8. CLI 接口

```bash
# 推送候选生成 + 发送（默认）
python -m feedback_hub.cli push

# 仅生成候选，打印消息体不发送（人工 review 用）
python -m feedback_hub.cli push --dry-run

# 仅生成候选，写 push_log 但不发送（用于自动化校验）
python -m feedback_hub.cli push --no-send

# 指定推送日期（默认今天；回灌后想补推某天的"如果当时推会推什么"）
python -m feedback_hub.cli push --date 2026-05-20 --dry-run

# 30 天历史回灌
bash feedback_hub/scripts/backfill.sh
```

---

## 9. 实施阶段（高层）

> 详细任务由后续 writing-plans 阶段产出。

| 阶段 | 内容 | 预计 |
|---|---|---|
| Q1 | `pusher/` 骨架：`kw_groups.yaml` + `signature.py` + `scorer.py` + 单元测试 | 0.5 天 |
| Q2 | `candidate.py`：SQL 拉取 + 与 §3 串通；用现有数据库本地跑出 Top N 给运营人工看 | 0.5 天 |
| Q3 | `formatter.py` + `webhook.py` + `push_log.py`，dry-run 与真实推送跑通 | 0.5 天 |
| Q4 | `schema.sql` 加 push_log + `cli.py` 加子命令 + `backfill.sh` | 0.5 天 |
| Q5 | 30 天回灌实跑 + 第一次 dry-run + 校准权重 | 0.5 天 |
| Q6 | 头两周人工 review 推送质量，迭代 `kw_groups.yaml` 与权重 | 持续 |

---

## 10. 风险与已知问题

| 风险 | 影响 | 缓解 |
|---|---|---|
| 关键词分组覆盖不全 | 真 Bug 没命中具体组 → 被排除 | 头两周看 dry-run 漏召的 case，迭代 yaml |
| `generic_bug` 让位规则误伤 | 同时命中 specific + generic 时丢失 generic 信息 | 业务上 generic 是噪声，丢失无害；保留 group_id 在 push_log 留 trail |
| 评分权重不准 | Top 5 排序失真 | 权重外置 yaml；头两周对照"运营心目中的 Top 5"调权重 |
| 30 分钟会话切分阈值不准导致 dup_count 失真 | 同一用户的两次反馈被算成两个 conv，热度被夸大 | 阶段 1 已有此风险；本期接受；MVP 后期看是否要在签名层做"同 user 同签名只算 1 票" |
| 拼接 full_text 含多组关键词 → 签名归类被优先级"压"到一个组 | 同会话多病症时，次要病症的信号丢失 | 接受，这是会话级聚合的代价；§2.5 已说明 |
| 拼接 full_text 过长（极端用户连发 20 条） | 关键词匹配冗余、展示重复 | 限制 SQL 端 GROUP_CONCAT 拼接前先 LIMIT msg 数（如取前 50 条）；展示用 `top_conf_text` 不受影响 |
| LLM 抽风把建议打成 P0 Bug | 推水货 | 11:55 人工 review；confidence≥0.7 已过滤一部分；持续观察 |
| 企微 webhook 限流 | 推送失败 | 3 次重试 + push_log 留 delivered_at=NULL，可人工补发 |
| MVP 头两周用得开心，后期没人看 | 推送变形式主义 | 这就是为什么要留 push_log——后期能用"开发是否在 TAPD 跟进过这些签名" 反查价值 |

---

## 11. 验收标准

- [ ] `kw_groups.yaml` 加载后 8 + 1 组关键词全部就位，正则/字符串匹配测试通过
- [ ] `signature.py` 单元测试覆盖：仅 generic_bug → None；specific + generic → 取 specific；多个 specific → 按优先级；版本号缺失 → `_unknown`
- [ ] **方案 X 召回 case**：构造一个 3 条消息的会话，首条 `"你好"`、第二条 `"我用 5.4.1"`、第三条 `"突然闪退了"`，期望签名命中 `crash` 组而非 None
- [ ] **代表文本来源 case**：同上会话，期望群里展示文本是第三条原文，而非首条 `"你好"`
- [ ] `scorer.py` 单元测试覆盖：4 个维度评分独立可验证；空输入返回空列表
- [ ] 用阶段 1 现有 conversation_label 数据本地跑一次 `push --dry-run`，输出至少 1 条候选（如果 7 天内有 P0/P1 Bug 数据）
- [ ] `push_log` 表能正常 insert，单日重复推送时不报错（v1 允许重复行，由 push_date+rank 区分；幂等留 v2）
- [ ] webhook dry-run 打印的 Markdown 在企微群手动粘贴能正确渲染
- [ ] `backfill.sh` 跑 30 天能完整跑完（允许个别天失败但记录在日志），跑完后 `feedback / message_label / conversation_label` 三表均有数据
- [ ] 0 候选当日的推送内容是"今日无 Top Bug"且 `push_log.is_empty=1`
- [ ] webhook 失败时 `push_log.delivered_at=NULL`，错误信息打到 stderr

---

## 12. 未来扩展（不在本期）

- **A → B 升级**：在企微卡片里加 review 链接 `dashboard/review/{push_log.id}`，开发点击后跳转打分页面（`is_real_bug` / `wow|meh`），数据回写 `push_feedback` 新表
- **签名物化**：当分组规则稳定后，把 signature 落到 `conversation_label.signature` 字段，索引化，提升查询速度
- **去重防霸榜**：同签名当周内已推过则降权或排除
- **按机器人分群**：不同 group 的 bug 推不同群（输入核心组 → 输入研发群，etc）
- **效果对账**：跨表 join push_log 与 TAPD 同步过来的 bug 单数据，自动算"推送命中率"
