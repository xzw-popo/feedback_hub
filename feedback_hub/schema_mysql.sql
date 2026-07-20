-- =============================================================================
-- feedback_hub/schema_mysql.sql —— MySQL 兼容版 DDL
-- 从 schema.sql 转换，主要差异：
--   INTEGER → INT, REAL → DOUBLE, AUTOINCREMENT → AUTO_INCREMENT
--   TEXT PRIMARY KEY → VARCHAR(255) PRIMARY KEY
--   无 PRAGMA，无 executescript
-- 重复执行幂等：所有建表/建索引语句使用 IF NOT EXISTS
-- =============================================================================

-- 1. 反馈消息（最细粒度，原样存）
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id     VARCHAR(255) PRIMARY KEY,
    conversation_id VARCHAR(255) NOT NULL,
    msg_seq         INT NOT NULL,
    channel         VARCHAR(64) NOT NULL,
    ts_ms           BIGINT NOT NULL,
    platform        VARCHAR(64),
    appversion      VARCHAR(64),
    user_vid        VARCHAR(255),
    service_vid     BIGINT,
    external_chat_url TEXT,
    keyboard_source VARCHAR(128),
    device_name     VARCHAR(255),
    channelid       VARCHAR(128),
    enginever       VARCHAR(128),
    msgtype         VARCHAR(32),
    text            TEXT NOT NULL,
    tags            TEXT,
    raw_json        MEDIUMTEXT,
    pulled_at       INT NOT NULL,
    INDEX idx_feedback_conv    (conversation_id, msg_seq),
    INDEX idx_feedback_user_ts (user_vid, ts_ms),
    INDEX idx_feedback_ts      (ts_ms)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 1a. 数据拉取覆盖区间（即使区间内 0 条反馈也要记录）
CREATE TABLE IF NOT EXISTS feedback_source_coverage (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    channel         VARCHAR(64) NOT NULL,
    start_ts_ms     BIGINT NOT NULL,
    end_ts_ms       BIGINT NOT NULL,
    completed_at_ms BIGINT NOT NULL,
    INDEX idx_feedback_coverage_channel_window
        (channel, start_ts_ms, end_ts_ms),
    UNIQUE INDEX uq_feedback_coverage_generation (completed_at_ms)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2. 消息级标签（保留每条消息的原始打标）
CREATE TABLE IF NOT EXISTS message_label (
    feedback_id     VARCHAR(255) PRIMARY KEY,
    L1              VARCHAR(64) NOT NULL,
    L2              TEXT,
    severity        VARCHAR(16) NOT NULL,
    confidence      DOUBLE NOT NULL,
    reason          TEXT,
    source          VARCHAR(32) NOT NULL,
    rule_name       VARCHAR(128),
    tagged_at       INT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3. 会话级聚合标签（前端默认查这张）
CREATE TABLE IF NOT EXISTS conversation_label (
    conversation_id VARCHAR(255) PRIMARY KEY,
    L1              VARCHAR(64) NOT NULL,
    L2              TEXT,
    severity        VARCHAR(16) NOT NULL,
    confidence      DOUBLE NOT NULL,
    reason          TEXT,
    source          VARCHAR(32) NOT NULL,
    msg_count       INT NOT NULL,
    first_ts_ms     BIGINT NOT NULL,
    last_ts_ms      BIGINT NOT NULL,
    user_vid        VARCHAR(255),
    appversion      VARCHAR(64),
    channel         VARCHAR(64) NOT NULL,
    service_vid     BIGINT,
    external_chat_url TEXT,
    aggregated_at   INT NOT NULL,
    INDEX idx_clabel_l1_ts     (L1, last_ts_ms),
    INDEX idx_clabel_ts        (last_ts_ms),
    INDEX idx_clabel_severity  (severity, last_ts_ms)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4. 标签变更历史（schema 预留，本期不写入）
CREATE TABLE IF NOT EXISTS label_history (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    target_type     VARCHAR(32) NOT NULL,
    target_id       VARCHAR(255) NOT NULL,
    L1              VARCHAR(64),
    L2              TEXT,
    severity        VARCHAR(16),
    confidence      DOUBLE,
    reason          TEXT,
    source          VARCHAR(32),
    operator        VARCHAR(64),
    created_at      INT NOT NULL,
    INDEX idx_history_target (target_type, target_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 5. 推送日志（spec 阶段 2 §5.1）
CREATE TABLE IF NOT EXISTS push_log (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    push_date       VARCHAR(16) NOT NULL,
    rank            INT NOT NULL,
    signature       TEXT,
    group_id        VARCHAR(128),
    primary_l2      VARCHAR(64),
    major_version   VARCHAR(64),
    representative_conversation_id VARCHAR(255),
    representative_feedback_id     VARCHAR(255),
    score           DOUBLE,
    dup_count       INT,
    p0_count        INT,
    cross_version   INT,
    affected_versions TEXT,
    representative_text TEXT,
    created_at      INT NOT NULL,
    delivered_at    INT,
    is_empty        INT NOT NULL DEFAULT 0,
    INDEX idx_pushlog_date    (push_date),
    INDEX idx_pushlog_sig     (signature(255)),
    INDEX idx_pushlog_repconv (representative_conversation_id),
    INDEX idx_pushlog_repmsg  (representative_feedback_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 6. 搜索报告任务
CREATE TABLE IF NOT EXISTS search_report_job (
    id                    VARCHAR(255) PRIMARY KEY,
    status                VARCHAR(32) NOT NULL,
    title                 VARCHAR(255) NOT NULL,
    query                 TEXT,
    search_type           VARCHAR(32) NOT NULL,
    filters_json          TEXT,
    search_payload_json   MEDIUMTEXT,
    conversation_ids_json MEDIUMTEXT NOT NULL,
    ai_scores_json        MEDIUMTEXT,
    sample_count          INT NOT NULL DEFAULT 0,
    result_markdown       MEDIUMTEXT,
    error_message         TEXT,
    created_at            BIGINT NOT NULL,
    started_at            BIGINT,
    finished_at           BIGINT,
    INDEX idx_report_job_created (created_at),
    INDEX idx_report_job_status (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
