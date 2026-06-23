CREATE TABLE IF NOT EXISTS weibo_post (
    id                  TEXT PRIMARY KEY,
    weibo_id            TEXT NOT NULL UNIQUE,
    source              TEXT NOT NULL DEFAULT 'weibo',
    url                 TEXT NOT NULL,
    author_id           TEXT,
    author_name         TEXT,
    author_verified     INTEGER NOT NULL DEFAULT 0,
    created_at_raw      TEXT,
    created_at_ms       INTEGER,
    text                TEXT NOT NULL,
    pic_urls_json       TEXT,
    reposts_count       INTEGER,
    comments_count      INTEGER,
    attitudes_count     INTEGER,
    first_seen_at       INTEGER NOT NULL,
    last_seen_at        INTEGER NOT NULL,
    raw_json            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_weibo_post_created ON weibo_post(created_at_ms);
CREATE INDEX IF NOT EXISTS idx_weibo_post_seen ON weibo_post(last_seen_at);

CREATE TABLE IF NOT EXISTS weibo_hit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    weibo_id        TEXT NOT NULL,
    keyword         TEXT NOT NULL,
    query_name      TEXT,
    searched_at     INTEGER NOT NULL,
    search_rank     INTEGER NOT NULL,
    source_mode     TEXT NOT NULL,
    FOREIGN KEY (weibo_id) REFERENCES weibo_post(weibo_id)
);
CREATE INDEX IF NOT EXISTS idx_weibo_hit_keyword ON weibo_hit(keyword, searched_at);

CREATE TABLE IF NOT EXISTS weibo_label (
    weibo_id        TEXT PRIMARY KEY,
    brand_focus     TEXT NOT NULL,
    sentiment       TEXT NOT NULL,
    topics_json     TEXT NOT NULL,
    post_type       TEXT NOT NULL,
    risk_level      TEXT NOT NULL,
    confidence      REAL,
    reason          TEXT,
    label_source    TEXT NOT NULL,
    labeled_at      INTEGER NOT NULL,
    FOREIGN KEY (weibo_id) REFERENCES weibo_post(weibo_id)
);
CREATE INDEX IF NOT EXISTS idx_weibo_label_brand ON weibo_label(brand_focus);
CREATE INDEX IF NOT EXISTS idx_weibo_label_sentiment ON weibo_label(sentiment);
CREATE INDEX IF NOT EXISTS idx_weibo_label_risk ON weibo_label(risk_level);

CREATE TABLE IF NOT EXISTS weibo_crawl_run (
    id                  TEXT PRIMARY KEY,
    status              TEXT NOT NULL,
    config_json         TEXT NOT NULL,
    started_at          INTEGER NOT NULL,
    finished_at         INTEGER,
    total_seen          INTEGER NOT NULL DEFAULT 0,
    inserted_posts      INTEGER NOT NULL DEFAULT 0,
    error_message       TEXT
);
CREATE INDEX IF NOT EXISTS idx_weibo_crawl_run_started ON weibo_crawl_run(started_at);
