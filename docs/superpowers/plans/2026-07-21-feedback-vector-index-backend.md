# Feedback Vector Index Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Productionize the local Qwen3-Embedding-0.6B experiment as a resumable feedback-level NumPy vector index and localhost HTTP search service.

**Architecture:** Keep raw feedback and vector metadata in the existing SQLite database, while storing normalized vectors in immutable `.npy` shards selected by an atomically replaced manifest. A dedicated localhost process keeps the embedding model loaded and serves the `/capabilities`, `/search`, and `/health` contract already consumed by `feedback_hub.topic_mining.vector_client`.

**Tech Stack:** Python 3.11, SQLite, NumPy, PyTorch CPU, Hugging Face Transformers, FastAPI, pytest.

## Global Constraints

- Index unit is one `feedback` row; retain `conversation_id` only as metadata.
- Dedupe identity is exact `feedback_id`; never merge same-text rows with different IDs.
- Model ID is exactly `Qwen3-Embedding-0.6B`; vectors are 1024-dimensional normalized `float32`.
- The index is derived data under `feedback_hub/data/vector_index/` and must remain outside git.
- Query and document encoding templates are versioned as part of `model_version`.
- No Qdrant, Milvus, FAISS, HNSW, Chroma, or new distributed service dependency in v1.
- The vector HTTP service binds to `127.0.0.1` and has no independent authentication layer.
- A failed build must never publish a partial shard or advance the vector watermark.

---

## File Structure

- Create `feedback_hub/vector_index/__init__.py`: public package exports.
- Create `feedback_hub/vector_index/config.py`: paths, model identity, dimensions, batch limits, and localhost port.
- Create `feedback_hub/vector_index/models.py`: shared immutable metadata and result dataclasses.
- Create `feedback_hub/vector_index/schema.sql`: SQLite-only vector metadata tables.
- Create `feedback_hub/vector_index/repository.py`: pending-row queries and transactional shard/run metadata writes.
- Create `feedback_hub/vector_index/encoder.py`: lazy Qwen model loading, last-token pooling, query instruction, normalization.
- Create `feedback_hub/vector_index/shards.py`: temp shard validation, checksum, manifest generations, compaction primitives.
- Create `feedback_hub/vector_index/sync.py`: resumable pending-feedback vectorization and shard publication.
- Create `feedback_hub/vector_index/search.py`: memory-mapped exact cosine search with hard metadata filters.
- Create `feedback_hub/vector_index/api.py`: localhost FastAPI contract used by `HttpVectorSearchClient`.
- Create `feedback_hub/vector_index/commands.py`: status, sync, rebuild, compact, and diagnostic-search command handlers.
- Create `scripts/vector_runtime.sh`: start/stop/status/log lifecycle for the vector process.
- Create `requirements-vector.txt`: pinned vector runtime dependencies.
- Modify `feedback_hub/cli.py`: add nested `vectors` commands without changing existing commands.
- Create tests under `feedback_hub/tests/test_vector_index_*.py` and `feedback_hub/tests/test_vector_runtime_script.py`.

### Task 1: Vector Metadata Repository

**Files:**
- Create: `feedback_hub/vector_index/__init__.py`
- Create: `feedback_hub/vector_index/config.py`
- Create: `feedback_hub/vector_index/models.py`
- Create: `feedback_hub/vector_index/schema.sql`
- Create: `feedback_hub/vector_index/repository.py`
- Test: `feedback_hub/tests/test_vector_index_repository.py`

**Interfaces:**
- Produces: `VectorIndexConfig`, `PendingFeedback`, `ShardMetadata`, `EmbeddingRecord`, `VectorRepository.init_schema()`, `pending_feedback()`, `begin_run()`, `publish_shard()`, `status()`.
- Consumes: existing SQLite `feedback` and `feedback_source_coverage` tables.

- [ ] **Step 1: Write failing repository tests**

```python
def test_pending_feedback_is_feedback_id_idempotent(tmp_path):
    repo = repository_fixture(tmp_path, feedback_rows=[
        feedback("f1", "相同文字"), feedback("f2", "相同文字")
    ])
    assert [row.feedback_id for row in repo.pending_feedback("qwen3-embedding-0.6b-document-v1", 10)] == ["f1", "f2"]
    repo.publish_shard(shard("s1"), [record("f1", "s1", 0)])
    assert [row.feedback_id for row in repo.pending_feedback("qwen3-embedding-0.6b-document-v1", 10)] == ["f2"]

def test_publish_shard_and_records_commit_atomically(tmp_path, monkeypatch):
    repo = repository_fixture(tmp_path, feedback_rows=[feedback("f1", "正文")])
    monkeypatch.setattr(repo, "_insert_record", lambda *args: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        repo.publish_shard(shard("s1"), [record("f1", "s1", 0)])
    assert repo.status().active_shards == 0
```

- [ ] **Step 2: Run the repository tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_vector_index_repository.py -q`

Expected: FAIL because `feedback_hub.vector_index.repository` does not exist.

- [ ] **Step 3: Add the vector configuration and SQLite schema**

```python
@dataclass(frozen=True)
class VectorIndexConfig:
    db_path: Path = feedback_config.DB_PATH
    data_dir: Path = feedback_config.DATA_DIR / "vector_index"
    model_dir: Path = feedback_config.DATA_DIR / "models" / "Qwen3-Embedding-0.6B"
    model_version: str = "qwen3-embedding-0.6b-document-v1"
    index_name: str = "feedback-items-v1"
    dimension: int = 1024
    batch_size: int = 8
    max_length: int = 512
    shard_size: int = 2048
    compact_after_shards: int = 72
    host: str = "127.0.0.1"
    port: int = 8011
```

Use the following concrete SQLite shape, followed by indexes on shard state and feedback/model lookup:

```sql
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
```

- [ ] **Step 4: Implement the repository transaction boundary**

```python
@dataclass(frozen=True)
class PendingFeedback:
    feedback_id: str
    conversation_id: str
    ts_ms: int
    platform: str
    channel: str
    appversion: str
    text: str
    content_hash: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PendingFeedback":
        text = str(row["text"])
        return cls(
            feedback_id=str(row["feedback_id"]),
            conversation_id=str(row["conversation_id"]),
            ts_ms=int(row["ts_ms"]),
            platform=str(row["platform"] or ""),
            channel=str(row["channel"] or ""),
            appversion=str(row["appversion"] or ""),
            text=text,
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )

@dataclass(frozen=True)
class ShardMetadata:
    shard_id: str
    model_version: str
    path: str
    dimension: int
    row_count: int
    checksum: str

@dataclass(frozen=True)
class EmbeddingRecord:
    feedback_id: str
    model_version: str
    content_hash: str
    shard_id: str
    row_offset: int
    embedded_at_ms: int

class VectorRepository:
    def pending_feedback(self, model_version: str, limit: int) -> list[PendingFeedback]:
        rows = self.connection.execute(
            """SELECT f.feedback_id, f.conversation_id, f.ts_ms, f.platform, f.channel,
                      f.appversion, f.text
               FROM feedback f
               WHERE TRIM(f.text) <> '' AND NOT EXISTS (
                   SELECT 1 FROM embedding_record er
                   WHERE er.feedback_id = f.feedback_id AND er.model_version = ?
               )
               ORDER BY f.ts_ms, f.feedback_id LIMIT ?""",
            (model_version, limit),
        ).fetchall()
        return [PendingFeedback.from_row(row) for row in rows]

    def begin_run(self, *, run_type: str, model_version: str) -> str:
        run_id = uuid.uuid4().hex
        self.connection.execute(
            "INSERT INTO embedding_sync_run (run_id, run_type, model_version, status, started_at_ms) VALUES (?, ?, ?, 'running', ?)",
            (run_id, run_type, model_version, now_ms()),
        )
        self.connection.commit()
        return run_id

    def publish_shard(self, shard: ShardMetadata, records: Sequence[EmbeddingRecord]) -> None:
        try:
            self.connection.execute(
                "INSERT INTO embedding_shard VALUES (?, ?, ?, ?, ?, ?, 'active', ?)",
                (shard.shard_id, shard.model_version, shard.path, shard.dimension, shard.row_count, shard.checksum, now_ms()),
            )
            self.connection.executemany(
                "INSERT INTO embedding_record VALUES (?, ?, ?, ?, ?, ?)",
                [(row.feedback_id, row.model_version, row.content_hash, row.shard_id, row.row_offset, row.embedded_at_ms) for row in records],
            )
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def finish_run(self, run_id: str, *, status: str, vectorized_count: int, error_code: str = "") -> None:
        self.connection.execute(
            "UPDATE embedding_sync_run SET status = ?, vectorized_count = ?, error_code = ?, finished_at_ms = ? WHERE run_id = ?",
            (status, vectorized_count, error_code, now_ms(), run_id),
        )
        self.connection.commit()
```

Compute `content_hash` as SHA-256 of the exact UTF-8 feedback text. `publish_shard()` must use one SQLite transaction for the shard row and every record row and must roll back on any exception.

- [ ] **Step 5: Run repository tests**

Run: `python3 -m pytest feedback_hub/tests/test_vector_index_repository.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit repository support**

```bash
git add feedback_hub/vector_index feedback_hub/tests/test_vector_index_repository.py
git commit -m "feat: add vector index metadata repository"
```

### Task 2: Qwen Encoder Adapter

**Files:**
- Create: `feedback_hub/vector_index/encoder.py`
- Test: `feedback_hub/tests/test_vector_index_encoder.py`
- Create: `requirements-vector.txt`

**Interfaces:**
- Produces: `EmbeddingEncoder` protocol and `QwenEmbeddingEncoder.encode_documents()` / `encode_queries()` returning `np.ndarray` with shape `(n, 1024)`.
- Consumes: `VectorIndexConfig.model_dir`, `.batch_size`, `.max_length`.

- [ ] **Step 1: Write failing pure-helper and fake-model tests**

```python
def test_instruct_query_is_stable():
    assert instruct_query("工具栏不隐藏") == (
        "Instruct: Given a user feedback search query for the WeChat keyboard/input method product, "
        "retrieve relevant user feedback.\nQuery:工具栏不隐藏"
    )

def test_encoder_rejects_wrong_dimension(fake_runtime, config):
    fake_runtime.output = np.zeros((1, 8), dtype=np.float32)
    with pytest.raises(ValueError, match="dimension"):
        QwenEmbeddingEncoder(config, runtime=fake_runtime).encode_documents(["正文"])
```

- [ ] **Step 2: Run encoder tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_vector_index_encoder.py -q`

Expected: FAIL because the encoder module is missing.

- [ ] **Step 3: Port only the verified embedding primitives from the lab**

```python
def last_token_pool(last_hidden_states: Tensor, attention_mask: Tensor) -> Tensor:
    if attention_mask[:, -1].sum() == attention_mask.shape[0]:
        return last_hidden_states[:, -1]
    sequence_lengths = attention_mask.sum(dim=1) - 1
    return last_hidden_states[torch.arange(last_hidden_states.shape[0], device=last_hidden_states.device), sequence_lengths]

def instruct_query(query: str) -> str:
    return f"Instruct: {QUERY_TASK}\nQuery:{query}"
```

Load `torch` and `transformers` lazily inside the concrete runtime so importing `feedback_hub` does not require vector dependencies. Force CPU on DevCloud, call `model.eval()`, use `torch.inference_mode()`, normalize on dimension 1, convert to finite `float32`, and verify exactly 1024 columns.

- [ ] **Step 4: Pin the CPU runtime dependency boundary**

```text
numpy==2.1.3
transformers==4.53.2
safetensors==0.5.3
```

Keep these in `requirements-vector.txt`, not the base `requirements.txt`, so normal dashboard development does not download vector dependencies. Install CPU PyTorch separately with `pip install --index-url https://download.pytorch.org/whl/cpu 'torch>=2.3,<3'`; the deployment smoke test must assert `torch.version.cuda is None`.

- [ ] **Step 5: Run encoder tests**

Run: `python3 -m pytest feedback_hub/tests/test_vector_index_encoder.py -q`

Expected: all tests PASS without loading the real model.

- [ ] **Step 6: Run one explicit local model smoke test**

Run:

```bash
python3 -c 'import json,numpy as np; from dataclasses import replace; from pathlib import Path; from feedback_hub.vector_index.config import VectorIndexConfig; from feedback_hub.vector_index.encoder import QwenEmbeddingEncoder; c=replace(VectorIndexConfig(), model_dir=Path("/Users/charvel/Desktop/用户反馈_2026_0612/feedback_hub/data/embedding_lab/models/Qwen3-Embedding-0.6B")); v=QwenEmbeddingEncoder(c).encode_queries(["工具栏不会隐藏"]); print(json.dumps({"shape":list(v.shape),"dtype":str(v.dtype),"norm":float(np.linalg.norm(v[0]))}))'
```

Expected: JSON reports `shape: [1, 1024]`, `dtype: float32`, and norm within `0.999..1.001`.

- [ ] **Step 7: Commit the encoder**

```bash
git add feedback_hub/vector_index/encoder.py requirements-vector.txt feedback_hub/tests/test_vector_index_encoder.py
git commit -m "feat: add Qwen feedback embedding encoder"
```

### Task 3: Immutable Shards and Atomic Manifest

**Files:**
- Create: `feedback_hub/vector_index/shards.py`
- Test: `feedback_hub/tests/test_vector_index_shards.py`

**Interfaces:**
- Produces: `write_shard() -> ShardMetadata`, `publish_manifest()`, `load_manifest()`, `verify_shard()`, `find_orphans()`.
- Consumes: normalized matrices from `EmbeddingEncoder` and metadata from `VectorRepository`.

- [ ] **Step 1: Write failing atomicity tests**

```python
def test_partial_shard_never_appears_in_manifest(tmp_path):
    store = ShardStore(tmp_path, dimension=1024, model_version="m1")
    with pytest.raises(ValueError, match="non-finite"):
        store.write_shard(np.full((1, 1024), np.nan, dtype=np.float32), ["f1"])
    assert not (tmp_path / "manifest.json").exists()

def test_manifest_switch_is_generation_atomic(tmp_path):
    store = ShardStore(tmp_path, dimension=2, model_version="m1")
    first = store.write_shard(normalized([[1, 0]]), ["f1"])
    store.publish_manifest([first], watermark_ts_ms=10)
    second = store.write_shard(normalized([[0, 1]]), ["f2"])
    store.publish_manifest([first, second], watermark_ts_ms=20)
    assert store.load_manifest().watermark_ts_ms == 20

def test_compaction_preserves_vectors_and_retires_old_shards_after_switch(vector_fixture):
    before = np.vstack([np.load(path, mmap_mode="r") for path in vector_fixture.active_shard_paths()])
    compact_active_shards(vector_fixture.repo, vector_fixture.store)
    after = np.vstack([np.load(path, mmap_mode="r") for path in vector_fixture.active_shard_paths()])
    np.testing.assert_allclose(after, before)
    assert vector_fixture.repo.status().active_shards == 1
    assert vector_fixture.repo.status().retired_shards == 2

def test_startup_rolls_forward_database_committed_manifest_pending_publication(vector_fixture):
    vector_fixture.fail_publication_after_database_commit()
    recovered = ShardStore.open(vector_fixture.config)
    assert recovered.load_manifest().generation == vector_fixture.expected_new_generation
    assert not recovered.publication_journal_path.exists()
```

- [ ] **Step 2: Run shard tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_vector_index_shards.py -q`

Expected: FAIL because `ShardStore` is missing.

- [ ] **Step 3: Implement temp-write, validation, checksum, and rename**

```python
def write_shard(self, vectors: np.ndarray, feedback_ids: Sequence[str]) -> ShardMetadata:
    validate_vectors(vectors, expected_rows=len(feedback_ids), dimension=self.dimension)
    temp = self.tmp_dir / f".{uuid.uuid4().hex}.npy"
    np.save(temp, vectors.astype(np.float32, copy=False), allow_pickle=False)
    checksum = sha256_file(temp)
    final = self.shards_dir / f"shard-{checksum[:16]}.npy"
    temp.replace(final)
    return ShardMetadata(shard_id=checksum[:24], model_version=self.model_version, path=str(final), row_count=len(feedback_ids), dimension=self.dimension, checksum=checksum)
```

Before changing SQLite mappings, write and fsync `publication-journal.json` containing the old manifest, complete new manifest, new shard checksums, and row remap. Commit SQLite, write the new manifest to `.manifest.json.tmp`, `flush()` and `os.fsync()`, then `replace()` the live file and remove the journal. The manifest must contain schema version, generation, model version, dimension, watermark, and ordered active shards.

- [ ] **Step 4: Implement startup validation and orphan discovery**

`recover_publication()` runs before `load_manifest()`: if a journal exists and SQLite contains the journal's new mappings, roll the manifest forward; otherwise restore the old manifest and remove unreferenced new files. `load_manifest()` then rejects unknown schema versions, wrong dimensions, missing files, checksum mismatches, duplicate shard IDs, and decreasing watermarks. `find_orphans()` returns only shard files absent from SQLite references, the live manifest, and the publication journal; it must not delete them.

- [ ] **Step 5: Implement generation-safe compaction**

`compact_active_shards()` memory-maps active shards, writes one validated replacement shard, writes the publication journal, transactionally updates existing `embedding_record.shard_id/row_offset` mappings and shard states, then replaces the manifest with the unchanged watermark. Existing requests retain their in-memory old state until the swap. A crash at any boundary is rolled forward or rolled back by `recover_publication()` before the service becomes healthy.

- [ ] **Step 6: Run shard tests**

Run: `python3 -m pytest feedback_hub/tests/test_vector_index_shards.py -q`

Expected: all tests PASS.

- [ ] **Step 7: Commit shard storage**

```bash
git add feedback_hub/vector_index/shards.py feedback_hub/tests/test_vector_index_shards.py
git commit -m "feat: add atomic NumPy vector shards"
```

### Task 4: Resumable Incremental Vector Sync

**Files:**
- Create: `feedback_hub/vector_index/sync.py`
- Test: `feedback_hub/tests/test_vector_index_sync.py`

**Interfaces:**
- Produces: `sync_pending(config, *, encoder=None, max_items=None) -> SyncResult` and `rebuild_index(config, *, target_model_version, target_generation_id, encoder=None) -> SyncResult`.
- Consumes: `VectorRepository`, `ShardStore`, and `EmbeddingEncoder` from Tasks 1–3.

```python
@dataclass(frozen=True)
class SyncResult:
    run_id: str
    vectorized_count: int
    pending_count: int
    watermark_ts_ms: int
```

- [ ] **Step 1: Write failing failure-recovery tests**

```python
def test_sync_retries_rows_left_unpublished_after_encoder_failure(fixture):
    with pytest.raises(RuntimeError, match="encoder failed"):
        sync_pending(fixture.config, encoder=FailOnceEncoder())
    assert fixture.repo.status().pending_count == 2
    result = sync_pending(fixture.config, encoder=DeterministicEncoder())
    assert result.vectorized_count == 2
    assert fixture.repo.status().pending_count == 0

def test_repeated_sync_does_not_reembed_existing_ids(fixture):
    encoder = CountingEncoder()
    sync_pending(fixture.config, encoder=encoder)
    sync_pending(fixture.config, encoder=encoder)
    assert encoder.document_count == len(fixture.feedback_rows)

def test_partial_backlog_does_not_advance_coverage_watermark(fixture):
    old_watermark = fixture.shards.load_manifest().watermark_ts_ms
    result = sync_pending(fixture.config, encoder=DeterministicEncoder(), max_items=1)
    assert result.pending_count == len(fixture.feedback_rows) - 1
    assert fixture.shards.load_manifest().watermark_ts_ms == old_watermark

def test_sync_compacts_after_configured_shard_threshold(fixture):
    fixture.config = replace(fixture.config, compact_after_shards=2, shard_size=1)
    sync_pending(fixture.config, encoder=DeterministicEncoder())
    assert fixture.repo.status().active_shards == 1
```

- [ ] **Step 2: Run sync tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_vector_index_sync.py -q`

Expected: FAIL because `sync_pending` is missing.

- [ ] **Step 3: Implement a single-writer sync loop**

```python
def sync_pending(config: VectorIndexConfig, *, encoder: EmbeddingEncoder | None = None, max_items: int | None = None) -> SyncResult:
    with process_lock(config.data_dir / ".sync.lock"):
        repo, shards = VectorRepository(config), ShardStore.from_config(config)
        run_id = repo.begin_run(run_type="incremental", model_version=config.model_version)
        rows = repo.pending_feedback(config.model_version, max_items or config.shard_size)
        vectors = (encoder or QwenEmbeddingEncoder(config)).encode_documents([row.text for row in rows])
        shard = shards.write_shard(vectors, [row.feedback_id for row in rows])
        repo.publish_shard(shard, embedding_records(rows, shard))
        remaining = repo.pending_count(config.model_version)
        watermark = repo.source_watermark_ms() if remaining == 0 else shards.current_watermark_ms()
        manifest = shards.publish_from_repository(repo, watermark_ts_ms=watermark)
        repo.finish_run(run_id, status="succeeded", vectorized_count=len(rows))
        return SyncResult(run_id, len(rows), remaining, manifest.watermark_ts_ms)
```

Loop in shard-sized chunks until no pending rows or `max_items` is reached. Advance the manifest watermark to the latest `feedback_source_coverage.completed_at_ms` only after `pending_count(model_version) == 0`; intermediate shard publication retains the prior watermark. After publication, call generation-safe compaction when active shard count reaches `compact_after_shards`. On exceptions, record `failed` without marking records complete. An empty sync must succeed without writing an empty shard and may advance the watermark after confirming the pending count is zero.

- [ ] **Step 4: Implement rebuild into a separate model generation**

`rebuild_index()` must require explicit non-empty `target_model_version` and safe `target_generation_id` values. It checkpoints under that stable generation directory, so a retry resumes only unpublished chunks. Promotion records both logical model and generation ID, and switches only after every non-empty feedback row has a published record. It must never clear the current active index first.

- [ ] **Step 5: Run sync tests**

Run: `python3 -m pytest feedback_hub/tests/test_vector_index_sync.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit incremental sync**

```bash
git add feedback_hub/vector_index/sync.py feedback_hub/tests/test_vector_index_sync.py
git commit -m "feat: add resumable vector synchronization"
```

### Task 5: Exact Search Engine and Localhost API

**Files:**
- Create: `feedback_hub/vector_index/search.py`
- Create: `feedback_hub/vector_index/api.py`
- Test: `feedback_hub/tests/test_vector_index_search.py`
- Test: `feedback_hub/tests/test_vector_index_api.py`
- Test: `feedback_hub/tests/test_vector_index_performance.py`

**Interfaces:**
- Produces: `VectorSearcher.reload_if_changed()`, `search(queries, filters, limit)`, FastAPI app factory `create_app(config, encoder=None)`, and module-level `app = create_app(VectorIndexConfig())`.
- Must match: `feedback_hub.topic_mining.vector_client.HttpVectorSearchClient` schema version 1.

- [ ] **Step 1: Write failing filter-before-ranking and API contract tests**

```python
def test_time_filter_is_applied_before_top_k(searcher):
    hits = searcher.search(
        [{"id": "q1", "text": "全屏工具栏", "kind": "positive"}],
        {"unit": "feedback", "start_ts_ms": 200, "end_ts_ms": 300},
        limit=1,
    )
    assert [hit.item_id for hit in hits] == ["in-window"]

def test_api_matches_existing_vector_client(test_client, topic_config):
    client = HttpVectorSearchClient(topic_config, request_fn=requests_adapter(test_client))
    assert client.capabilities().supported_units == ("feedback",)
    assert client.search([{"id": "q1", "text": "工具栏", "kind": "positive"}], {"unit": "feedback"}, 10).hits
```

- [ ] **Step 2: Run search/API tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_vector_index_search.py feedback_hub/tests/test_vector_index_api.py -q`

Expected: FAIL because the searcher and API do not exist.

- [ ] **Step 3: Implement memory-mapped exact cosine search**

```python
scores = np.einsum("d,nd->n", query_vector, eligible_vectors, dtype=np.float32)
top = np.argpartition(-scores, min(limit, len(scores)) - 1)[:limit]
ranked = top[np.argsort(-scores[top], kind="stable")]
```

Build the eligible row offsets from SQLite metadata before scoring. Support `start_ts_ms`, `end_ts_ms`, `platforms`, `channels`, `products`, `versions`, and only `unit=feedback`. Reject unknown filters rather than ignore them. Merge per-shard Top-K results globally with deterministic `item_id` tie-breaking.

- [ ] **Step 4: Implement manifest hot reload**

Check manifest generation before each request. Load and validate a complete new generation into a temporary immutable state object, then swap the reference under a lock. Requests already holding the old state finish on the old generation.

- [ ] **Step 5: Implement the localhost FastAPI contract**

```python
@app.get("/capabilities")
def capabilities(index: str) -> dict[str, object]:
    if index != config.index_name:
        raise HTTPException(status_code=404, detail="unknown vector index")
    manifest = searcher.current_manifest()
    return {"index": index, "schema_version": 1, "supported_units": ["feedback"], "watermark_ts_ms": manifest.watermark_ts_ms}

@app.post("/search")
def search(payload: SearchPayload) -> dict[str, object]:
    if payload.index != config.index_name:
        raise HTTPException(status_code=404, detail="unknown vector index")
    result = searcher.search(payload.queries, payload.filters, payload.limit)
    return {"index": payload.index, "watermark_ts_ms": result.watermark_ts_ms, "hits": [hit.to_dict() for hit in result.hits]}

@app.get("/health")
def health() -> dict[str, object]:
    state = searcher.health()
    if not state.ready:
        raise HTTPException(status_code=503, detail=state.error_code)
    return state.to_dict()
```

Responses must include the configured index and source coverage generation as `watermark_ts_ms`. Never expose vector files, raw model paths, or full exception traces.

- [ ] **Step 6: Run search/API tests and the existing client suite**

Run: `python3 -m pytest feedback_hub/tests/test_vector_index_search.py feedback_hub/tests/test_vector_index_api.py feedback_hub/tests/test_topic_mining_vector_client.py -q`

Expected: all tests PASS.

- [ ] **Step 7: Record a 127k-row opt-in performance baseline**

Create `test_vector_index_performance.py` with a `@pytest.mark.performance` fixture that writes 127,000 normalized 1024-dimensional vectors in deterministic chunks, times cold manifest load and ten exact searches, and prints JSON containing `cold_load_seconds`, `query_p50_ms`, and `query_p95_ms`. Assert only correct Top-K cardinality, finite scores, and deterministic repeated IDs; preserve observed latency as PR evidence rather than inventing an SLA.

Run: `python3 -m pytest feedback_hub/tests/test_vector_index_performance.py -m performance -q -s`

Expected: PASS and one JSON metrics object is printed.

- [ ] **Step 8: Commit the search service**

```bash
git add feedback_hub/vector_index/search.py feedback_hub/vector_index/api.py feedback_hub/tests/test_vector_index_search.py feedback_hub/tests/test_vector_index_api.py feedback_hub/tests/test_vector_index_performance.py
git commit -m "feat: serve exact feedback vector search"
```

### Task 6: Operational CLI and Vector Runtime

**Files:**
- Create: `feedback_hub/vector_index/commands.py`
- Modify: `feedback_hub/cli.py`
- Create: `scripts/vector_runtime.sh`
- Test: `feedback_hub/tests/test_vector_index_commands.py`
- Test: `feedback_hub/tests/test_vector_runtime_script.py`

**Interfaces:**
- Produces CLI: `vectors sync|status|rebuild|compact|search|serve|smoke-encode`.
- Consumes Tasks 1–5 and starts `uvicorn feedback_hub.vector_index.api:app` on localhost.

- [ ] **Step 1: Write failing parser and runtime lifecycle tests**

```python
def test_vector_status_cli_is_json(capsys, vector_fixture):
    assert main(["vectors", "status", "--db", str(vector_fixture.db)]) == 0
    assert json.loads(capsys.readouterr().out)["model_version"] == "qwen3-embedding-0.6b-document-v1"

def test_vector_runtime_binds_only_loopback():
    body = VECTOR_RUNTIME.read_text(encoding="utf-8")
    assert "--host 127.0.0.1" in body
    assert "vector_index.pid" in body
```

- [ ] **Step 2: Run CLI/runtime tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_vector_index_commands.py feedback_hub/tests/test_vector_runtime_script.py -q`

Expected: FAIL because the commands and script are missing.

- [ ] **Step 3: Add nested vector subcommands to the existing parser**

```python
vectors = sub.add_parser("vectors", help="管理反馈向量索引")
vector_sub = vectors.add_subparsers(dest="vector_command", required=True)
for name in ("sync", "status", "rebuild", "compact", "search", "serve", "smoke-encode"):
    vector_sub.add_parser(name)
vectors.set_defaults(func=cmd_vectors)
```

Command handlers print exactly one JSON object to stdout, write diagnostics to stderr, and return non-zero for lock contention, unhealthy manifests, missing models, or incomplete rebuilds.

- [ ] **Step 4: Add the process lifecycle script**

`scripts/vector_runtime.sh` must mirror `scripts/devcloud_runtime.sh` with separate PID/log files, install `requirements-vector.txt`, bind only `127.0.0.1:${VECTOR_PORT:-8011}`, and provide `start|stop|restart|status|logs`. It must refuse to start when the model directory or active manifest is missing, except in an explicit `VECTOR_ALLOW_EMPTY_INDEX=1` bootstrap mode.

- [ ] **Step 5: Run CLI/runtime tests**

Run: `python3 -m pytest feedback_hub/tests/test_vector_index_commands.py feedback_hub/tests/test_vector_runtime_script.py feedback_hub/tests/test_cli.py -q`

Expected: all tests PASS and existing CLI commands remain unchanged.

- [ ] **Step 6: Run the complete vector backend suite**

Run: `python3 -m pytest feedback_hub/tests/test_vector_index_repository.py feedback_hub/tests/test_vector_index_encoder.py feedback_hub/tests/test_vector_index_shards.py feedback_hub/tests/test_vector_index_sync.py feedback_hub/tests/test_vector_index_search.py feedback_hub/tests/test_vector_index_api.py feedback_hub/tests/test_vector_index_commands.py feedback_hub/tests/test_vector_runtime_script.py feedback_hub/tests/test_topic_mining_vector_client.py -q`

Expected: all tests PASS.

- [ ] **Step 7: Commit operational entry points**

```bash
git add feedback_hub/cli.py feedback_hub/vector_index/commands.py scripts/vector_runtime.sh feedback_hub/tests/test_vector_index_commands.py feedback_hub/tests/test_vector_runtime_script.py
git commit -m "feat: add vector index operational commands"
```

## Plan Acceptance Gate

- A fixture database can be indexed twice without duplicate records or vectors.
- A forced encoder or file-write failure leaves the previous manifest queryable.
- The existing `HttpVectorSearchClient` passes against the new localhost API.
- Hard filters are applied before vector Top-K selection.
- The real local Qwen model produces normalized 1024-dimensional vectors.
- No model, vector file, or feedback data appears in git status.
