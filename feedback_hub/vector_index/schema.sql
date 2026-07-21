CREATE TABLE IF NOT EXISTS embedding_shard (
    shard_id TEXT PRIMARY KEY,
    model_version TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    dimension INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('building', 'active', 'retired')),
    created_at_ms INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_embedding_shard_state ON embedding_shard(state);

CREATE TABLE IF NOT EXISTS embedding_record (
    feedback_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    shard_id TEXT NOT NULL,
    row_offset INTEGER NOT NULL,
    embedded_at_ms INTEGER NOT NULL,
    PRIMARY KEY (feedback_id, model_version, content_hash),
    FOREIGN KEY (feedback_id) REFERENCES feedback(feedback_id),
    FOREIGN KEY (shard_id) REFERENCES embedding_shard(shard_id)
);
CREATE INDEX IF NOT EXISTS idx_embedding_record_feedback_model
    ON embedding_record(feedback_id, model_version);

CREATE TABLE IF NOT EXISTS embedding_sync_run (
    run_id TEXT PRIMARY KEY,
    run_type TEXT NOT NULL,
    model_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'partial', 'failed', 'skipped_locked')),
    started_at_ms INTEGER NOT NULL,
    finished_at_ms INTEGER,
    window_start_ms INTEGER,
    window_end_ms INTEGER,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    duplicate_count INTEGER NOT NULL DEFAULT 0,
    vectorized_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT NOT NULL DEFAULT ''
);
