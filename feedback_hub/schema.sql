-- =============================================================================
-- feedback_hub/schema.sql —— 数据中枢 4 张表 DDL
-- 严格对齐 spec §4.2
-- 重复执行幂等：所有建表/建索引语句使用 IF NOT EXISTS
-- =============================================================================

-- 1. 反馈消息（最细粒度，原样存）
CREATE TABLE IF NOT EXISTS feedback (
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
CREATE INDEX IF NOT EXISTS idx_feedback_conv    ON feedback(conversation_id, msg_seq);
CREATE INDEX IF NOT EXISTS idx_feedback_user_ts ON feedback(user_vid, ts_ms);
CREATE INDEX IF NOT EXISTS idx_feedback_ts      ON feedback(ts_ms);

-- 2. 消息级标签（保留每条消息的原始打标）
CREATE TABLE IF NOT EXISTS message_label (
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
CREATE TABLE IF NOT EXISTS conversation_label (
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
CREATE INDEX IF NOT EXISTS idx_clabel_l1_ts     ON conversation_label(L1, last_ts_ms);
CREATE INDEX IF NOT EXISTS idx_clabel_ts        ON conversation_label(last_ts_ms);
CREATE INDEX IF NOT EXISTS idx_clabel_severity  ON conversation_label(severity, last_ts_ms);

-- 4. 标签变更历史（schema 预留，本期不写入）
CREATE TABLE IF NOT EXISTS label_history (
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
CREATE INDEX IF NOT EXISTS idx_history_target ON label_history(target_type, target_id);
