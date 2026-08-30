# Mining Feedback Topics Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify an internally distributable `mining-feedback-topics` Codex Skill plus the minimal `feedback_hub` backend that turns a natural-language feedback topic into an audited, read-only hybrid-retrieval run.

**Architecture:** The Skill converts the request into one versioned `topic_spec`, validates it locally, and calls a stable internal HTTP API. A new isolated `feedback_hub.topic_mining` package snapshots the source database read-only, performs BM25 plus vector-service recall, classifies candidates through the existing resumable model-route runner, builds a bounded review queue, and exports only verified results. The Skill and backend share one JSON Schema; no database, model, secret, or topic-specific rule is bundled with the Skill.

**Tech Stack:** Python 3.11, FastAPI/Pydantic, SQLite, JSON/JSONL, `requests`, existing `feedback_hub.topic_discovery.model_routes`, `openpyxl`, pytest, Codex Skill `SKILL.md`/`agents/openai.yaml`.

## Global Constraints

- The distributable Skill is named exactly `mining-feedback-topics` and lives at `codex-skills/mining-feedback-topics/`.
- Run the no-Skill baseline evaluation before creating any file under `codex-skills/mining-feedback-topics/`.
- The first release is internal-only and requires the internal topic-mining backend.
- The source feedback database is opened read-only; snapshots and run state live under `feedback_hub/data/topic_mining/` and never enter Git.
- Do not call the general tagger and do not write `message_label`, `conversation_label`, `label_history`, or source `feedback` rows.
- Vector similarity only recalls candidates. It never determines final membership.
- Metadata filters run before semantic retrieval; every vector result is intersected with the hard-scoped item IDs.
- The caller never chooses SQL, vector collection, similarity threshold, Top-K, batch size, concurrency, or classifier model.
- Every final item must point to source text, evidence, source URL, run version, and data cutoff.
- Unresolved coverage, vector-watermark, classifier, parser, duplicate-ID, or missing-link failures block a complete export.
- Existing unrelated worktree changes belong to the user. Stage and commit only the files listed in the current task.

---

### Task 1: Capture the no-Skill baseline before authoring the Skill

**Files:**
- Create: `docs/superpowers/evals/mining-feedback-topics/scenarios.json`
- Create: `docs/superpowers/evals/mining-feedback-topics/baseline.md`

**Interfaces:**
- Consumes: three user-style requests and the evaluation rubric below; no Skill files or intended solution text
- Produces: immutable baseline transcripts and observed failure categories used by Task 8

- [ ] **Step 1: Add the exact evaluation scenarios and rubric**

Create `scenarios.json` with this content:

```json
{
  "scenarios": [
    {
      "id": "toolbar-clear-scope",
      "prompt": "找最近半年 Win 端反馈：一类是输入法工具栏位置乱跑，另一类是视频或游戏全屏、无边框全屏或独占全屏时工具栏不隐藏。要原文和链接，不要改正式标签。请说明你会如何调用现有内部反馈和向量工具完成。"
    },
    {
      "id": "adjacent-concepts",
      "prompt": "找 Win 上‘那个条挡着内容’的反馈。输入法工具栏算，Windows 系统任务栏和输入法候选框不算。请使用内部反馈检索能力完成，并保留可审计的纳入排除依据。"
    },
    {
      "id": "missing-scope",
      "prompt": "帮我找语音输入结束后文字不上屏的反馈，使用内部反馈和向量检索能力。"
    }
  ],
  "rubric": {
    "required": [
      "states explicit inclusion and exclusion boundaries",
      "keeps metadata filters separate from semantic recall",
      "treats vector similarity as recall only",
      "uses a structured topic specification",
      "requires source evidence and links",
      "reports data coverage and unresolved failures",
      "does not write formal labels"
    ],
    "failure_codes": [
      "missing_negative_boundary",
      "vector_as_final_judge",
      "bottom_layer_parameters_exposed",
      "no_audit_trail",
      "unnecessary_blocking_question",
      "silent_partial_result",
      "formal_label_writeback"
    ]
  }
}
```

- [ ] **Step 2: Verify that no Skill implementation exists**

Run:

```bash
test ! -e codex-skills/mining-feedback-topics/SKILL.md
```

Expected: exit code `0`. If the file already exists, stop and move that untested implementation out of the workspace before running the baseline; do not read or reuse it.

- [ ] **Step 3: Run three fresh-context baseline agents without the Skill**

Dispatch one fresh agent per scenario with only that scenario's `prompt`. Do not pass the design spec, rubric, desired architecture, or any expected answer. Use `fork_turns="none"`. Save each complete answer verbatim under its scenario heading in `baseline.md`.

After the transcripts, add a table with columns `scenario`, `failure_code`, `evidence_excerpt`, and `present`. Score only behavior visible in the transcript; do not infer hidden intent.

- [ ] **Step 4: Confirm that the baseline exposes a real gap**

Run:

```bash
rg -n "present: true|\| true \|" docs/superpowers/evals/mining-feedback-topics/baseline.md
```

Expected: at least one failure is present. If none is present, stop Skill authoring and report that the control already satisfies the proposed guidance; do not create redundant Skill instructions.

- [ ] **Step 5: Commit the baseline evidence**

```bash
git add docs/superpowers/evals/mining-feedback-topics/scenarios.json docs/superpowers/evals/mining-feedback-topics/baseline.md
git commit -m "test: capture feedback topic skill baseline"
```

---

### Task 2: Define the canonical topic specification and local validation

**Files:**
- Create: `feedback_hub/topic_mining/__init__.py`
- Create: `feedback_hub/topic_mining/contracts.py`
- Test: `feedback_hub/tests/test_topic_mining_contracts.py`

**Interfaces:**
- Consumes: `Mapping[str, Any]` created by the Skill or API request
- Produces: `TopicSpec`, `validate_topic_spec(raw) -> TopicSpec`, `topic_spec_hash(spec) -> str`, and `topic_spec_json_schema() -> dict[str, Any]`

- [ ] **Step 1: Write failing contract tests**

Create `test_topic_mining_contracts.py`:

```python
from datetime import datetime

import pytest

from feedback_hub.topic_mining.contracts import (
    topic_spec_hash,
    topic_spec_json_schema,
    validate_topic_spec,
)


def valid_spec():
    return {
        "schema_version": 1,
        "topic_name": "全屏时工具栏不隐藏",
        "objective": "找出视频或游戏全屏时输入法工具栏仍显示的反馈",
        "scope": {
            "start_time": "2026-01-16T00:00:00+08:00",
            "end_time": "2026-07-16T14:00:00+08:00",
            "platforms": ["Win"],
            "products": ["微信输入法"],
        },
        "unit": "feedback",
        "inclusion_criteria": ["视频或游戏全屏时工具栏仍显示"],
        "exclusion_criteria": ["Windows 系统任务栏不隐藏"],
        "positive_examples": ["全屏游戏时输入法工具条一直挡着"],
        "negative_examples": ["进入游戏后黑屏"],
        "lexical_hints": {"objects": ["工具栏"], "contexts": ["全屏", "游戏"]},
        "classification_labels": [
            {"id": "matched", "meaning": "明确符合专题定义"},
            {"id": "not_matched", "meaning": "不符合或证据不足"},
        ],
        "output": {
            "preferred_format": "xlsx",
            "required_fields": ["feedback_text", "feedback_time", "source_url"],
        },
    }


def test_validate_topic_spec_normalizes_timezone_and_deduplicates_terms():
    raw = valid_spec()
    raw["lexical_hints"]["contexts"].append("全屏")
    spec = validate_topic_spec(raw)
    assert spec.scope.start_time == datetime.fromisoformat("2026-01-16T00:00:00+08:00")
    assert spec.lexical_hints["contexts"] == ("全屏", "游戏")


@pytest.mark.parametrize("field", ["topic_name", "objective", "inclusion_criteria", "exclusion_criteria"])
def test_validate_topic_spec_rejects_missing_semantic_boundary(field):
    raw = valid_spec()
    raw.pop(field)
    with pytest.raises(ValueError, match=field):
        validate_topic_spec(raw)


def test_validate_topic_spec_rejects_naive_or_reversed_time():
    raw = valid_spec()
    raw["scope"]["start_time"] = "2026-01-16T00:00:00"
    with pytest.raises(ValueError, match="timezone"):
        validate_topic_spec(raw)
    raw = valid_spec()
    raw["scope"]["end_time"] = "2026-01-15T00:00:00+08:00"
    with pytest.raises(ValueError, match="end_time"):
        validate_topic_spec(raw)


def test_topic_spec_hash_is_stable_under_mapping_order():
    left = valid_spec()
    right = dict(reversed(list(left.items())))
    assert topic_spec_hash(validate_topic_spec(left)) == topic_spec_hash(validate_topic_spec(right))


def test_schema_forbids_bottom_layer_retrieval_parameters():
    schema = topic_spec_json_schema()
    encoded = str(schema)
    assert "top_k" not in encoded
    assert "similarity_threshold" not in encoded
    assert schema["additionalProperties"] is False
```

- [ ] **Step 2: Run the contract tests and verify RED**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_contracts.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'feedback_hub.topic_mining'`.

- [ ] **Step 3: Implement immutable contract objects and strict validation**

In `contracts.py`, use frozen dataclasses and these exact public fields:

```python
@dataclass(frozen=True)
class TopicScope:
    start_time: datetime
    end_time: datetime
    platforms: tuple[str, ...] = ()
    products: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    versions: tuple[str, ...] = ()


@dataclass(frozen=True)
class TopicSpec:
    schema_version: int
    topic_name: str
    objective: str
    scope: TopicScope
    unit: str
    inclusion_criteria: tuple[str, ...]
    exclusion_criteria: tuple[str, ...]
    positive_examples: tuple[str, ...]
    negative_examples: tuple[str, ...]
    lexical_hints: dict[str, tuple[str, ...]]
    classification_labels: tuple[dict[str, str], ...]
    output: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "topic_name": self.topic_name,
            "objective": self.objective,
            "scope": {
                "start_time": self.scope.start_time.isoformat(),
                "end_time": self.scope.end_time.isoformat(),
                "platforms": list(self.scope.platforms),
                "products": list(self.scope.products),
                "channels": list(self.scope.channels),
                "versions": list(self.scope.versions),
            },
            "unit": self.unit,
            "inclusion_criteria": list(self.inclusion_criteria),
            "exclusion_criteria": list(self.exclusion_criteria),
            "positive_examples": list(self.positive_examples),
            "negative_examples": list(self.negative_examples),
            "lexical_hints": {key: list(value) for key, value in sorted(self.lexical_hints.items())},
            "classification_labels": [dict(value) for value in self.classification_labels],
            "output": dict(self.output),
        }
```

Implement `validate_topic_spec` so it rejects unknown top-level and scope fields, accepts only `schema_version=1`, `unit in {"feedback", "conversation"}`, `preferred_format in {"xlsx", "jsonl"}`, aware ISO-8601 datetimes with `start < end`, non-empty unique label IDs containing exactly `matched` and `not_matched`, and non-empty strings after stripping. Deduplicate list values while preserving order. Implement the hash as SHA-256 of canonical UTF-8 JSON using `sort_keys=True` and compact separators.

Return a JSON Schema with `additionalProperties: false` at every object boundary and no retrieval-engine parameters. Add a `python -m feedback_hub.topic_mining.contracts --write-schema PATH` entry point that writes this schema deterministically for Task 7.

- [ ] **Step 4: Run the contract tests and verify GREEN**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_contracts.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit the canonical contract**

```bash
git add feedback_hub/topic_mining/__init__.py feedback_hub/topic_mining/contracts.py feedback_hub/tests/test_topic_mining_contracts.py
git commit -m "feat: define feedback topic specification"
```

---

### Task 3: Build isolated run storage and read-only source snapshots

**Files:**
- Create: `feedback_hub/topic_mining/config.py`
- Create: `feedback_hub/topic_mining/run_store.py`
- Create: `feedback_hub/topic_mining/source.py`
- Test: `feedback_hub/tests/test_topic_mining_run_store.py`
- Test: `feedback_hub/tests/test_topic_mining_source.py`

**Interfaces:**
- Consumes: a validated `TopicSpec`, source SQLite path, and `TopicMiningConfig`
- Produces: `TopicRunStore`, `create_source_snapshot(...) -> SourceSnapshot`, `fetch_scoped_items(...) -> list[dict]`, and `build_item_contexts(...) -> dict[str, list[dict]]`

- [ ] **Step 1: Write failing run-store and source tests**

Cover these behaviors with fixtures that create a temporary source database containing only a `feedback` table:

```python
def test_create_run_is_idempotent_for_spec_and_source_watermark(tmp_path, valid_topic_spec):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    first = store.create_or_get(valid_topic_spec, source_watermark_ms=1234)
    second = store.create_or_get(valid_topic_spec, source_watermark_ms=1234)
    assert first["run_id"] == second["run_id"]
    assert first["status"] == "pending"


def test_same_spec_with_new_source_watermark_creates_new_run(tmp_path, valid_topic_spec):
    store = TopicRunStore(tmp_path / "runs.db", tmp_path / "runs")
    assert store.create_or_get(valid_topic_spec, 1234)["run_id"] != store.create_or_get(valid_topic_spec, 5678)["run_id"]


def test_snapshot_and_scope_do_not_modify_source(tmp_path, source_db, valid_topic_spec):
    before = source_db.read_bytes()
    snapshot = create_source_snapshot(source_db, tmp_path / "snapshot.db")
    items = fetch_scoped_items(snapshot.path, valid_topic_spec)
    assert source_db.read_bytes() == before
    assert {item["platform"] for item in items} == {"Win"}


def test_build_feedback_context_uses_same_user_within_30_minutes(scoped_feedback_items):
    contexts = build_item_contexts(scoped_feedback_items, unit="feedback", window_ms=1_800_000)
    assert [row["feedback_id"] for row in contexts["f2"]] == ["f1", "f2", "f3"]
```

Also test half-open time bounds, platform/channel/version filters, blank-text exclusion, `feedback_id` uniqueness, conversation aggregation ordering, source URL fallback through `db.build_external_chat_url`, and a missing source date range.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining_run_store.py feedback_hub/tests/test_topic_mining_source.py -q
```

Expected: FAIL because the modules do not exist.

- [ ] **Step 3: Implement configuration and the independent run database**

Define `TopicMiningConfig` with these defaults and environment overrides:

```python
@dataclass(frozen=True)
class TopicMiningConfig:
    source_db_path: Path = feedback_config.DB_PATH
    data_dir: Path = Path(os.environ.get(
        "TOPIC_MINING_DATA_DIR",
        str(feedback_config.DATA_DIR / "topic_mining"),
    ))
    vector_api_url: str = os.environ.get("TOPIC_VECTOR_API_URL", "")
    vector_api_token: str = os.environ.get("TOPIC_VECTOR_API_TOKEN", "")
    vector_index: str = os.environ.get("TOPIC_VECTOR_INDEX", "feedback-items-v1")
    vector_max_lag_seconds: int = int(os.environ.get("TOPIC_VECTOR_MAX_LAG_SECONDS", "21600"))
    api_token: str = os.environ.get("TOPIC_MINING_API_TOKEN", "")
    bm25_top_k: int = 1000
    vector_top_k: int = 1000
    candidate_limit: int = 1500
    rrf_k: int = 60
    classifier_batch_size: int = 20
    classifier_concurrency: int = 8
```

`TopicRunStore` must create a separate `runs.db` with one `topic_run` table containing `run_id`, `spec_hash`, `spec_json`, `source_watermark_ms`, `status`, `stage`, `error_code`, `error_message`, `artifact_dir`, `created_at_ms`, `updated_at_ms`, and `manifest_json`. Allow only these states: `pending`, `running`, `review_ready`, `verified`, `failed`, and `paused_quota_exhausted`. Use atomic transactions and a run ID derived from `sha256(spec_hash + ':' + source_watermark_ms)[:16]`. `create_or_get` returns the run mapping plus `created: true` only for the transaction that inserted it; repeated calls return `created: false`, allowing the API to start exactly one worker.

- [ ] **Step 4: Implement SQLite backup and normalized source items**

Open the source through `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`. Create the run snapshot with SQLite's `Connection.backup()` into the run artifact directory. Record SHA-256, byte size, `MIN(ts_ms)`, `MAX(ts_ms)`, and row count in `SourceSnapshot`; never copy or mutate the formal database directly.

Return one normalized mapping per requested unit with these stable keys:

```python
{
    "item_id": str,
    "feedback_id": str | None,
    "conversation_id": str,
    "ts_ms": int,
    "platform": str,
    "appversion": str,
    "channel": str,
    "device_name": str,
    "user_vid": str,
    "text": str,
    "source_url": str,
}
```

For `unit=conversation`, concatenate messages in `msg_seq, ts_ms, feedback_id` order and use the conversation ID as `item_id`. For `unit=feedback`, use `feedback_id`. Apply scope filters with parameterized SQL and half-open timestamps. Missing required coverage raises `DataCoverageError(start_ms, end_ms, source_min_ms, source_max_ms)` before retrieval.

- [ ] **Step 5: Run the focused tests and verify GREEN**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining_run_store.py feedback_hub/tests/test_topic_mining_source.py -q
```

Expected: all tests PASS and the temporary source database hash is unchanged.

- [ ] **Step 6: Commit storage and source isolation**

```bash
git add feedback_hub/topic_mining/config.py feedback_hub/topic_mining/run_store.py feedback_hub/topic_mining/source.py feedback_hub/tests/test_topic_mining_run_store.py feedback_hub/tests/test_topic_mining_source.py
git commit -m "feat: isolate topic mining runs and snapshots"
```

---

### Task 4: Add auditable BM25 and vector-service hybrid recall

**Files:**
- Create: `feedback_hub/topic_mining/vector_client.py`
- Create: `feedback_hub/topic_mining/retrieval.py`
- Test: `feedback_hub/tests/test_topic_mining_vector_client.py`
- Test: `feedback_hub/tests/test_topic_mining_retrieval.py`

**Interfaces:**
- Consumes: scoped source items, `TopicSpec`, `TopicMiningConfig`, and vector service responses
- Produces: `VectorCapabilities`, `VectorHit`, `RecallHit`, `HttpVectorSearchClient`, and `hybrid_recall(...) -> RecallPlan`

- [ ] **Step 1: Write failing vector-contract and fusion tests**

Add tests for this exact provider contract:

```python
def test_vector_client_sends_semantic_queries_and_metadata_scope(fake_http, config):
    client = HttpVectorSearchClient(config, request_fn=fake_http)
    client.search(
        queries=[{"id": "positive:0", "text": "全屏时工具栏不隐藏", "kind": "positive"}],
        filters={"unit": "feedback", "start_ts_ms": 1, "end_ts_ms": 9, "platforms": ["Win"]},
        limit=1000,
    )
    assert fake_http.json["index"] == "feedback-items-v1"
    assert fake_http.json["queries"][0]["kind"] == "positive"
    assert fake_http.json["filters"]["platforms"] == ["Win"]


def test_hybrid_recall_unions_channels_but_intersects_hard_scope(valid_topic_spec):
    items = [
        {"item_id": "in-scope", "text": "游戏全屏工具栏挡着"},
        {"item_id": "keyword-only", "text": "悬浮栏位置乱跑"},
    ]
    vector_hits = [
        VectorHit("in-scope", "positive:0", 0.81, 1),
        VectorHit("out-of-scope", "positive:0", 0.99, 2),
    ]
    plan = hybrid_recall(items, valid_topic_spec, vector_hits=vector_hits, config=test_config())
    assert {row.item_id for row in plan.candidates} == {"in-scope", "keyword-only"}
    assert "out-of-scope" in plan.rejected_out_of_scope_ids


def test_vector_only_candidate_is_not_marked_as_final_match(valid_topic_spec):
    plan = hybrid_recall(
        [{"item_id": "v1", "text": "那个条一直挡着"}],
        valid_topic_spec,
        vector_hits=[VectorHit("v1", "positive:0", 0.82, 1)],
        config=test_config(),
    )
    assert plan.candidates[0].channels == ("vector",)
    assert not hasattr(plan.candidates[0], "matched")


def test_stale_vector_watermark_blocks_hybrid_recall(config):
    with pytest.raises(VectorIndexStaleError):
        verify_vector_watermark(source_watermark_ms=10_000_000, vector_watermark_ms=1, max_lag_seconds=3600)
```

Also test deterministic tokenization, BM25 ranking, RRF tie-breaking by `item_id`, negative-query hit flags, duplicate vector IDs, non-finite scores, missing watermark, and the configured candidate limit.

- [ ] **Step 2: Run retrieval tests and verify RED**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining_vector_client.py feedback_hub/tests/test_topic_mining_retrieval.py -q
```

Expected: FAIL because retrieval modules are missing.

- [ ] **Step 3: Implement the vector-service adapter**

`GET {TOPIC_VECTOR_API_URL}/capabilities?index=...` must return:

```json
{
  "index": "feedback-items-v1",
  "schema_version": 1,
  "supported_units": ["feedback", "conversation"],
  "watermark_ts_ms": 1784191200000
}
```

`POST {TOPIC_VECTOR_API_URL}/search` receives the JSON asserted in Step 1 and returns:

```json
{
  "index": "feedback-items-v1",
  "watermark_ts_ms": 1784191200000,
  "hits": [
    {"item_id": "feedback-id", "query_id": "positive:0", "score": 0.81, "rank": 1}
  ]
}
```

Use `requests.Session`, a 30-second timeout, and `Authorization: Bearer ...` only when a token is configured. Reject unknown schema versions, duplicate `(query_id, item_id)` pairs, missing IDs, non-finite scores, non-positive ranks, and responses from the wrong index. Redact tokens from raised messages.

- [ ] **Step 4: Implement deterministic BM25, query construction, and RRF**

Port the tested tokenization/BM25 behavior from `feedback_hub/data/embedding_lab/rrf_search_conversation_embedding_index.py` into production code; do not import from the ignored data directory.

Build semantic queries in this order:

1. `objective:0` from `spec.objective` as kind `positive`;
2. `positive:N` from each positive example;
3. `negative:N` from each negative example as kind `negative`.

Build the BM25 query from the objective, inclusion criteria, and flattened lexical hints. Retrieve the configured top-K from each positive channel. Use reciprocal-rank fusion:

```python
score = sum(1.0 / (config.rrf_k + rank) for rank in positive_ranks)
```

Negative ranks do not add recall score; record them as `negative_query_hits`. Sort by descending fused score, then ascending `item_id`. Persist `recall_candidates.jsonl` and `recall_manifest.json` with channel ranks, raw scores, query IDs, rejected out-of-scope IDs, configuration version, source watermark, and vector watermark.

- [ ] **Step 5: Run retrieval tests and verify GREEN**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining_vector_client.py feedback_hub/tests/test_topic_mining_retrieval.py -q
```

Expected: all tests PASS.

- [ ] **Step 6: Commit hybrid recall**

```bash
git add feedback_hub/topic_mining/vector_client.py feedback_hub/topic_mining/retrieval.py feedback_hub/tests/test_topic_mining_vector_client.py feedback_hub/tests/test_topic_mining_retrieval.py
git commit -m "feat: add auditable hybrid topic recall"
```

---

### Task 5: Add resumable semantic classification and a bounded review queue

**Files:**
- Create: `feedback_hub/topic_mining/classifier.py`
- Create: `feedback_hub/topic_mining/review.py`
- Create: `feedback_hub/topic_mining/prompts/classify_system.md`
- Test: `feedback_hub/tests/test_topic_mining_classifier.py`
- Test: `feedback_hub/tests/test_topic_mining_review.py`

**Interfaces:**
- Consumes: `TopicSpec`, `RecallHit` rows, normalized items, contexts, and existing `ModelRoute` objects
- Produces: `ClassificationResult`, `classify_candidates(...)`, `build_review_queue(...)`, and `apply_review_overrides(...)`

- [ ] **Step 1: Write failing prompt, parser, checkpoint, and review tests**

Use tests that assert:

```python
def test_prompt_contains_topic_boundaries_but_not_retrieval_scores(valid_topic_spec):
    prompt = build_classification_prompt(valid_topic_spec, [candidate_payload()])
    assert "Windows 系统任务栏不隐藏" in prompt
    assert "全屏时输入法工具栏仍显示" in prompt
    assert "rrf_score" not in prompt
    assert "vector_score" not in prompt


def test_parser_requires_exact_candidate_coverage(valid_topic_spec):
    raw = '{"results":[{"item_id":"a","label":"matched","confidence":0.9,"evidence":["工具栏一直显示"],"reason":"全屏未隐藏","needs_review":false}]}'
    with pytest.raises(ValueError, match="exact coverage"):
        parse_classification_reply(raw, expected_ids={"a", "b"}, allowed_labels={"matched", "not_matched"})


def test_parser_rejects_evidence_not_present_in_text():
    with pytest.raises(ValueError, match="evidence"):
        validate_evidence([result(evidence=["不存在的事实"])], {"a": candidate_payload(text="原文")})


def test_review_queue_includes_vector_only_match_and_negative_conflict():
    queue = build_review_queue(
        run_id="topic_1",
        classifications=[matched("a", 0.91), matched("b", 0.92)],
        recall_by_id={"a": recall(channels=("vector",)), "b": recall(negative_query_hits=("negative:0",))},
        per_label_sample=0,
    )
    assert {row["item_id"] for row in queue} == {"a", "b"}


def test_overrides_are_separate_and_cannot_change_source_text():
    merged = apply_review_overrides(
        [matched("a", 0.6)],
        [{"item_id": "a", "label": "not_matched", "reason": "系统任务栏", "reviewer": "skill-ai"}],
    )
    assert merged[0].label == "not_matched"
    assert merged[0].source == "review_override"
```

Also test JSON-fence repair, confidence bounds, allowed labels, duplicate IDs, empty evidence for matched items, retry after parser failure, resumable batches, quota pause, and deterministic reject/random samples.

- [ ] **Step 2: Run classification tests and verify RED**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining_classifier.py feedback_hub/tests/test_topic_mining_review.py -q
```

Expected: FAIL because classifier and review modules are missing.

- [ ] **Step 3: Add the strict classification prompt and parser**

The system prompt must require one JSON object, exact item coverage, only provided labels, evidence copied from candidate text or context, and `not_matched` when evidence is insufficient. It must explicitly state that retrieval rank and vector similarity are not membership evidence.

Batch at most `classifier_batch_size=20`. Build jobs as `(batch_index, batch_key, payload)` and run them through `run_pauseable_model_jobs` with `classifier_concurrency` workers and existing `call_model_route`. On a parse error, append the parser message and allowed schema to the prompt and retry once. Flatten only complete batch outputs into `classified.jsonl`; keep raw batch replies, route, model, attempts, elapsed time, and retry chain in `classification_audit.jsonl`. Any unresolved batch keeps the run from reaching `review_ready`.

Load the default classifier route from existing `LLM_API_URL`, `LLM_API_KEY`, and `LLM_MODEL`. Reject execution before scheduling if any required value is empty; never serialize credentials into a run artifact.

- [ ] **Step 4: Implement deterministic review selection and overrides**

Include all of these mandatory reasons:

- `low_confidence_match` when `label=matched` and confidence `< 0.75`;
- `vector_only_match` when a matched item has no lexical/BM25 channel;
- `negative_query_conflict` when a matched item appears in any negative query;
- `classifier_requested_review` when the classifier sets `needs_review=true`;
- `deterministic_label_sample` for up to 20 hash-selected items per label;
- `high_confidence_reject_sample` for up to 20 rejects with confidence `>= 0.85`.

Hash-select with `sha256(f"{run_id}:{item_id}:{reason}")` so reruns are stable. Store `review_queue.jsonl`; store overrides separately as `review_overrides.jsonl`. Require override `item_id`, allowed `label`, non-empty `reason`, and `reviewer`; reject duplicate or unknown IDs.

- [ ] **Step 5: Run classification and review tests and verify GREEN**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining_classifier.py feedback_hub/tests/test_topic_mining_review.py -q
```

Expected: all tests PASS.

- [ ] **Step 6: Commit classification and review**

```bash
git add feedback_hub/topic_mining/classifier.py feedback_hub/topic_mining/review.py feedback_hub/topic_mining/prompts/classify_system.md feedback_hub/tests/test_topic_mining_classifier.py feedback_hub/tests/test_topic_mining_review.py
git commit -m "feat: classify and review topic candidates"
```

---

### Task 6: Orchestrate runs, verify exports, and expose the internal API

**Files:**
- Create: `feedback_hub/topic_mining/export.py`
- Create: `feedback_hub/topic_mining/service.py`
- Create: `feedback_hub/topic_mining/api.py`
- Create: `requirements-topic-mining.txt`
- Modify: `feedback_hub/api.py`
- Modify: `.env.example`
- Test: `feedback_hub/tests/test_topic_mining_export.py`
- Test: `feedback_hub/tests/test_topic_mining_service.py`
- Test: `feedback_hub/tests/test_topic_mining_api.py`

**Interfaces:**
- Consumes: all Task 2–5 contracts and artifacts
- Produces: `run_topic_job(run_id)`, `verify_topic_run(run_id)`, XLSX/JSONL artifacts, and `/api/topic-mining/*`

- [ ] **Step 1: Write failing export, service, and API tests**

Cover the following end-to-end state contract:

```python
def test_verify_blocks_unresolved_classifier_failure(run_fixture):
    run_fixture.write_manifest(unresolved_classifier_items=1)
    with pytest.raises(RunVerificationError, match="unresolved_classifier_items"):
        verify_topic_run(run_fixture.run_id, store=run_fixture.store)


def test_export_has_unique_ids_links_and_evidence(run_fixture):
    path = export_topic_run(run_fixture.run_id, "xlsx", store=run_fixture.store)
    wb = openpyxl.load_workbook(path, read_only=False, data_only=False)
    ws = wb["反馈清单"]
    assert [cell.value for cell in ws[1]][:6] == ["命中分类", "反馈时间", "反馈原文", "对应链接", "判定理由", "证据"]
    assert ws["D2"].hyperlink.target.startswith("https://")


def test_create_run_is_idempotent_and_starts_background_job(client, monkeypatch):
    started = []
    monkeypatch.setattr("feedback_hub.topic_mining.api.start_run_async", started.append)
    first = client.post("/api/topic-mining/runs", json=valid_spec()).json()
    second = client.post("/api/topic-mining/runs", json=valid_spec()).json()
    assert first["run_id"] == second["run_id"]
    assert started == [first["run_id"]]


def test_api_token_is_required_when_configured(client_with_token):
    response = client_with_token.get("/api/topic-mining/capabilities")
    assert response.status_code == 401
```

Also test capabilities, run status, review queue retrieval, override validation, export before verification, safe artifact filenames, missing run IDs, vector staleness, data coverage failures, quota pause, and that source bytes do not change after a complete fake-backed run.

- [ ] **Step 2: Run service/API tests and verify RED**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining_export.py feedback_hub/tests/test_topic_mining_service.py feedback_hub/tests/test_topic_mining_api.py -q
```

Expected: FAIL because service, export, and API modules are missing.

- [ ] **Step 3: Implement the run state machine**

`run_topic_job` must execute these persisted stages in order and resume from the first missing valid artifact:

```text
snapshot -> hard_scope -> hybrid_recall -> classify -> review_queue -> review_ready
```

Persist an atomic `manifest.json` after each stage with input/output counts, artifact SHA-256 values, source/vector watermarks, model route/model names, retry totals, unresolved counts, and internal retrieval configuration. On expected quota exhaustion set `paused_quota_exhausted`; on other failures set `failed` with a stable `error_code` and redacted message. Never mark `review_ready` with unresolved classification or parser failures.

After overrides, `verify_topic_run` must check exact candidate classification coverage, unique item IDs, allowed labels, source evidence containment, valid source URLs, source scope, manifest hash reconciliation, no unresolved failures, and review override validity. A successful verify sets status `verified`.

- [ ] **Step 4: Implement deterministic JSONL and XLSX exports**

Export only final `matched` rows. Always write `final_results.jsonl` and `quality_report.json`; create XLSX when requested. Use one `反馈清单` sheet, freeze `A2`, enable filters, wrap text, and create clickable links. Include columns:

```text
命中分类, 反馈时间, 反馈原文, 对应链接, 判定理由, 证据,
平台, 版本, 设备, Feedback ID, Conversation ID, Run ID
```

Sort by classification label and descending timestamp. Reject duplicate IDs, missing text, missing links, missing evidence, invalid scope, invalid labels, and formula-like strings beginning with `=`, `+`, `-`, or `@`; prefix such user text with a single quote before writing XLSX to prevent formula injection.

Add `requirements-topic-mining.txt`:

```text
-r requirements.txt
openpyxl==3.1.5
```

- [ ] **Step 5: Expose the high-level HTTP API**

Register a router at `/api/topic-mining` with:

```text
GET  /capabilities
POST /runs
GET  /runs/{run_id}
GET  /runs/{run_id}/review-queue
POST /runs/{run_id}/overrides
POST /runs/{run_id}/verify
POST /runs/{run_id}/export
GET  /runs/{run_id}/artifacts/{artifact_name}
```

Follow `feedback_hub.search.report_api`: create a daemon thread only for a newly pending run, return immediately, and make status polling explicit. If `TOPIC_MINING_API_TOKEN` is non-empty, require `Authorization: Bearer <token>` for every route. Artifact names must be selected from the run manifest rather than joined from arbitrary user input.

Add the router to `create_app` in `feedback_hub/api.py`. Add the six `TOPIC_*` variables from `TopicMiningConfig` to `.env.example` with blank tokens and explanatory comments; do not add real endpoints or credentials.

- [ ] **Step 6: Run focused and existing API tests**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining_export.py feedback_hub/tests/test_topic_mining_service.py feedback_hub/tests/test_topic_mining_api.py feedback_hub/tests/test_search_api.py -q
```

Expected: all tests PASS.

- [ ] **Step 7: Commit the complete backend entry point**

```bash
git add feedback_hub/topic_mining/export.py feedback_hub/topic_mining/service.py feedback_hub/topic_mining/api.py requirements-topic-mining.txt feedback_hub/api.py .env.example feedback_hub/tests/test_topic_mining_export.py feedback_hub/tests/test_topic_mining_service.py feedback_hub/tests/test_topic_mining_api.py
git commit -m "feat: expose verified topic mining runs"
```

---

### Task 7: Initialize the distributable Skill and add deterministic client tools

**Files:**
- Create: `codex-skills/mining-feedback-topics/SKILL.md`
- Create: `codex-skills/mining-feedback-topics/agents/openai.yaml`
- Create: `codex-skills/mining-feedback-topics/scripts/validate_topic_spec.py`
- Create: `codex-skills/mining-feedback-topics/scripts/topic_backend_client.py`
- Create: `codex-skills/mining-feedback-topics/references/topic-spec.schema.json`
- Test: `feedback_hub/tests/test_mining_feedback_topics_skill_package.py`

**Interfaces:**
- Consumes: canonical schema and `/api/topic-mining` contract
- Produces: standalone validator CLI and backend client CLI used by `SKILL.md`

- [ ] **Step 1: Read the Skill metadata contract and write failing package tests**

Read `/Users/charvel/.codex/skills/.system/skill-creator/references/openai_yaml.md` completely before choosing interface values.

Create package tests that initially fail because the Skill directory is absent:

```python
def test_skill_schema_matches_backend_contract():
    bundled = json.loads((SKILL_ROOT / "references/topic-spec.schema.json").read_text())
    assert bundled == topic_spec_json_schema()


def test_validator_accepts_valid_json_and_rejects_bottom_layer_parameter(tmp_path):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(valid_spec(), ensure_ascii=False))
    assert run_validator(good).returncode == 0
    bad = valid_spec()
    bad["top_k"] = 100
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps(bad, ensure_ascii=False))
    result = run_validator(bad_path)
    assert result.returncode == 2
    assert "unknown field: top_k" in result.stderr


def test_client_uses_environment_url_and_redacts_token(monkeypatch):
    monkeypatch.setenv("FEEDBACK_TOPIC_API_URL", "https://topic.internal")
    monkeypatch.setenv("FEEDBACK_TOPIC_API_TOKEN", "secret-value")
    result = run_client("capabilities", fake_urlopen=raising_urlopen("secret-value"))
    assert "secret-value" not in result.stderr


def test_skill_frontmatter_has_only_name_and_description():
    metadata = parse_frontmatter(SKILL_ROOT / "SKILL.md")
    assert set(metadata) == {"name", "description"}
    assert metadata["name"] == "mining-feedback-topics"
    assert metadata["description"].startswith("Use when")
```

- [ ] **Step 2: Run package tests and verify RED**

Run: `python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py -q`

Expected: FAIL because `codex-skills/mining-feedback-topics` does not exist.

- [ ] **Step 3: Initialize the Skill with the official generator**

Run:

```bash
python3 /Users/charvel/.codex/skills/.system/skill-creator/scripts/init_skill.py mining-feedback-topics --path codex-skills --resources scripts,references --interface 'display_name=反馈专题挖掘' --interface 'short_description=从内部反馈中提取可审计的专题结果' --interface 'default_prompt=使用 $mining-feedback-topics 从内部反馈中查找我描述的专题，并保留证据和链接。'
```

Expected: the folder contains `SKILL.md`, `agents/openai.yaml`, `scripts/`, and `references/`. Delete all generated example or placeholder resource files before continuing.

- [ ] **Step 4: Generate and bundle the canonical JSON Schema**

Run:

```bash
python3 -m feedback_hub.topic_mining.contracts --write-schema codex-skills/mining-feedback-topics/references/topic-spec.schema.json
```

Expected: a deterministic UTF-8 JSON file equal to `topic_spec_json_schema()`.

- [ ] **Step 5: Implement the standalone validator**

The CLI accepts one `.json`, `.yaml`, or `.yml` path, loads YAML through PyYAML only for YAML suffixes, validates all object/list/scalar rules represented by the bundled schema, and prints canonical JSON to stdout. Exit `0` on success, `2` on validation errors, and never echo the full input. Resolve the schema relative to `__file__`, not the current directory.

Expose this usage:

```text
validate_topic_spec.py TOPIC_SPEC_PATH
```

Its error output must name the JSON path and problem, for example `$.scope.start_time: timezone is required` or `$: unknown field: top_k`.

- [ ] **Step 6: Implement the standard-library backend client**

Use `urllib.request` so the Skill does not add a runtime HTTP dependency. Read `FEEDBACK_TOPIC_API_URL` and optional `FEEDBACK_TOPIC_API_TOKEN`; allow `--base-url` for tests. Implement these subcommands and JSON stdout:

```text
capabilities
create-run --spec PATH
get-run RUN_ID
review-queue RUN_ID --output PATH
apply-overrides RUN_ID --file PATH
verify RUN_ID
export RUN_ID --format xlsx|jsonl
download RUN_ID ARTIFACT_NAME --output PATH
```

Use a 30-second timeout for ordinary calls and atomic `.tmp -> replace` writes for downloaded files. Convert HTTP errors into exit code `3`, transport errors into `4`, and local validation/filesystem errors into `2`. Redact the configured token from every error.

- [ ] **Step 7: Run package tests and verify machine resources are GREEN**

Run: `python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py -q`

Expected: all machine-resource and metadata tests PASS. The behavior assertions for the final SKILL workflow remain excluded until Task 8.

- [ ] **Step 8: Commit the initialized Skill tools**

```bash
git add codex-skills/mining-feedback-topics feedback_hub/tests/test_mining_feedback_topics_skill_package.py
git commit -m "feat: initialize feedback topic mining skill"
```

---

### Task 8: Write the minimal Skill guidance from baseline failures and forward-test it

**Files:**
- Modify: `codex-skills/mining-feedback-topics/SKILL.md`
- Create: `codex-skills/mining-feedback-topics/references/topic-spec.md`
- Create: `codex-skills/mining-feedback-topics/references/backend-contract.md`
- Create: `codex-skills/mining-feedback-topics/references/review-policy.md`
- Create: `docs/superpowers/evals/mining-feedback-topics/with-skill.md`
- Modify: `feedback_hub/tests/test_mining_feedback_topics_skill_package.py`

**Interfaces:**
- Consumes: baseline failures, bundled scripts, and the backend contract
- Produces: concise workflow guidance that makes fresh agents satisfy the same rubric

- [ ] **Step 1: Add behavior-shape tests before editing SKILL.md**

Add assertions that the final Skill:

- stays under 500 words excluding frontmatter;
- links directly to all three references and both scripts;
- contains the ordered gates `capabilities`, `validate`, `create`, `inspect`, `review`, `verify`, `export`, `deliver`;
- requires data coverage, vector watermark, exact classification coverage, and source evidence;
- never contains a toolbar-specific keyword list or a fixed historical result count;
- does not contain secrets, production URLs, SQL, Top-K, or similarity thresholds.

Run the focused test and confirm it fails against the generated placeholder Skill.

- [ ] **Step 2: Write the Skill discovery metadata and core workflow**

Use this exact frontmatter:

```yaml
---
name: mining-feedback-topics
description: Use when a user needs to mine, collect, or audit a one-off feedback issue or topic from internal feedback data, especially when strict inclusion/exclusion boundaries, semantic recall, source evidence, links, or a downloadable result are required.
---
```

Write the body in imperative form. Its core principle is: “Let the backend execute reliable data work; use AI judgment only to define the topic and review semantic boundaries.” Give the caller this ordered contract:

1. Read `references/topic-spec.md`; call capabilities before promising coverage.
2. Convert the request into a topic spec; ask one question only when a missing scope choice materially changes the result.
3. Run `scripts/validate_topic_spec.py`; repair all errors before submission.
4. Create one idempotent run and poll it; never create replacements for a running ID.
5. Inspect funnel counts, data cutoff, vector watermark, and unresolved failures.
6. Read `references/review-policy.md`; review only the bounded queue and submit separate overrides.
7. Verify before export; deliver requested artifacts with cutoff, counts, and limitations.

State stop conditions positively: a complete deliverable has zero coverage gaps, zero stale-index violations, exact candidate classification coverage, verified source evidence, and valid links. If those conditions are absent, report the run as incomplete.

- [ ] **Step 3: Write the three focused references**

`topic-spec.md` must define every field, distinguish hard scope from semantic criteria, explain positive/negative examples and lexical hints, and include one non-toolbar example such as “语音输入结束后文字不上屏.” It must tell the agent not to invent product/platform scope.

`backend-contract.md` must list the client commands, environment variables, statuses, stable error categories, artifact names, and the rule that retrieval internals are backend-owned. It must not include credentials or a production hostname.

`review-policy.md` must explain each mandatory review reason, require decisions from source text/context rather than similarity, define override fields, and state that overrides never edit source records.

- [ ] **Step 4: Run structural tests and official Skill validation**

Run:

```bash
python3 /Users/charvel/.codex/skills/.system/skill-creator/scripts/generate_openai_yaml.py codex-skills/mining-feedback-topics --interface 'display_name=反馈专题挖掘' --interface 'short_description=从内部反馈中提取可审计的专题结果' --interface 'default_prompt=使用 $mining-feedback-topics 从内部反馈中查找我描述的专题，并保留证据和链接。'
python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py -q
python3 /Users/charvel/.codex/skills/.system/skill-creator/scripts/quick_validate.py codex-skills/mining-feedback-topics
wc -w codex-skills/mining-feedback-topics/SKILL.md
```

Expected: tests PASS, validator prints success, and the SKILL body remains below 500 words.

- [ ] **Step 5: Forward-test the same three scenarios with the Skill**

Dispatch three fresh agents with `fork_turns="none"`. Each prompt must be exactly:

```text
Use $mining-feedback-topics at <absolute-skill-path> to handle this request:
<original scenario prompt>
```

Do not send the rubric, baseline failures, or desired answer. Save full outputs verbatim in `with-skill.md`, then score the same rubric used in `baseline.md`.

- [ ] **Step 6: Refactor only against observed forward-test gaps**

For each remaining failure, classify it before editing:

- wrong output shape: add a positive ordered recipe;
- omitted required field: add it to the structural contract or reference table;
- conditional mistake: key the instruction to an observable condition;
- deliberate rule violation: add an explicit prohibition and the observed rationalization.

Re-run only the affected scenario in a fresh context, append the new transcript, and stop once all required rubric items pass without exposing bottom-layer parameters. Do not add speculative guidance unrelated to observed failures.

- [ ] **Step 7: Commit the verified Skill guidance**

```bash
git add codex-skills/mining-feedback-topics docs/superpowers/evals/mining-feedback-topics/with-skill.md feedback_hub/tests/test_mining_feedback_topics_skill_package.py
git commit -m "feat: teach auditable feedback topic mining"
```

---

### Task 9: Run end-to-end verification and prepare distribution

**Files:**
- Modify: `WORKSPACE_GUIDE.md`
- Modify: `docs/devcloud-container-deployment.md`
- Test: `feedback_hub/tests/test_topic_mining_end_to_end.py`

**Interfaces:**
- Consumes: completed backend, Skill package, temporary source fixture, fake vector provider, and fake classifier route
- Produces: one verified fixture run, repository documentation, and a distributable Skill folder with no runtime contamination

- [ ] **Step 1: Write a failing end-to-end fixture test**

Create a temporary feedback database with at least these records:

- a Win game-fullscreen toolbar match;
- a Win Windows-taskbar reject;
- a Win game-black-screen reject;
- a Mac semantic match excluded by hard scope;
- a Win paraphrase retrieved only by the fake vector provider.

The test must create a run through the service, inject a fake vector client and fake model route, assert the funnel, apply one override, verify, export XLSX, and compare source SHA-256 before/after. Assert that the vector-only paraphrase reaches classification, the Mac row never does, and only verified matched Win rows enter the workbook.

- [ ] **Step 2: Run the end-to-end test and verify RED**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_end_to_end.py -q`

Expected: FAIL at the first missing integration or dependency-injection seam.

- [ ] **Step 3: Add only the integration seams required by the failing test**

Make `run_topic_job` accept optional `vector_client`, `model_routes`, and `model_call_fn` keyword arguments used by tests; production defaults still come from config. Keep all source, run-store, and export paths explicit. Do not add test-only behavior to public API responses.

- [ ] **Step 4: Run the end-to-end and full focused suite**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining_contracts.py feedback_hub/tests/test_topic_mining_run_store.py feedback_hub/tests/test_topic_mining_source.py feedback_hub/tests/test_topic_mining_vector_client.py feedback_hub/tests/test_topic_mining_retrieval.py feedback_hub/tests/test_topic_mining_classifier.py feedback_hub/tests/test_topic_mining_review.py feedback_hub/tests/test_topic_mining_export.py feedback_hub/tests/test_topic_mining_service.py feedback_hub/tests/test_topic_mining_api.py feedback_hub/tests/test_mining_feedback_topics_skill_package.py feedback_hub/tests/test_topic_mining_end_to_end.py -q
```

Expected: all tests PASS with no warnings about unresolved runs or leaked resources.

- [ ] **Step 5: Scan the distributable package for forbidden content**

Run:

```bash
rg --files codex-skills/mining-feedback-topics | sort
rg -n -i "api[_-]?key|authorization:|bearer [a-z0-9]|wrfeedback|devcloud|feedback\.db|qwen3|top_k|similarity_threshold|工具栏" codex-skills/mining-feedback-topics
```

Expected: only intended Skill files are listed. The content scan has no secret, production host, database path, model bundle, bottom-layer parameter, or toolbar-specific hit. Generic mentions of `Authorization` in client source are allowed only if the token value comes from the environment and is never printed.

- [ ] **Step 6: Document the new maintained areas**

Update `WORKSPACE_GUIDE.md` so `codex-skills/` is a tracked distributable Skill source rather than the ignored root `skills/` cache. Document `feedback_hub/topic_mining/`, its separate data directory, read-only source rule, optional API token, vector-service dependency, and `requirements-topic-mining.txt`.

Update `docs/devcloud-container-deployment.md` with installation of `requirements-topic-mining.txt`, the six `TOPIC_*` environment variables, health verification through `/api/topic-mining/capabilities`, and the explicit rule that deployment does not upload Skill files into the service or replace the production database.

- [ ] **Step 7: Run repository checks and Skill validation**

Run:

```bash
python3 -m pytest feedback_hub/tests -q
python3 /Users/charvel/.codex/skills/.system/skill-creator/scripts/quick_validate.py codex-skills/mining-feedback-topics
git diff --check
```

Expected: all backend tests PASS, Skill validation succeeds, and `git diff --check` produces no output.

- [ ] **Step 8: Commit the integration and distribution documentation**

```bash
git add feedback_hub/tests/test_topic_mining_end_to_end.py feedback_hub/topic_mining/service.py docs/devcloud-container-deployment.md
git commit -m "test: verify distributable topic mining skill"
```

If `git ls-files --error-unmatch WORKSPACE_GUIDE.md` succeeds, include `WORKSPACE_GUIDE.md` in that commit. If it was already an untracked user-owned file before this feature, keep the required directory-map update in the working tree but do not stage it; disclose that fact in the handoff.

- [ ] **Step 9: Record the final handoff facts**

Run:

```bash
git status --short
git log --oneline --max-count=10
```

Report the Skill source path, backend API prefix, required environment variables, exact tests run, forward-test scenarios, and any unrelated pre-existing worktree changes. Do not claim production deployment unless it was separately authorized and verified.
