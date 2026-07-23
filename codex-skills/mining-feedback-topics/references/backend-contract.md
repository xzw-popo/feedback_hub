# Backend Contract

Use [`topic_backend_client.py`](../scripts/topic_backend_client.py). The client defaults to `http://charvelxia-any2.devcloud.woa.com:8000`. Both URL overrides are optional: pass `--base-url` or set non-empty `FEEDBACK_TOPIC_API_URL`. The current internal-network/VPN deployment advertises `authentication=internal_network_boundary`, so `FEEDBACK_TOPIC_API_TOKEN` is unused; preserve the optional client support only for a future deployment that explicitly requires authorization. Do not place either value in a topic spec, artifact, or transcript.

| Command | Purpose |
| --- | --- |
| `capabilities` | Require exact caller-AI `classification_protocol` v2, `supported_units=["feedback"]`, then check schema, formats, read-only mode, statuses, platform aliases, time defaults, budgets, authentication, and freshness. Standard uses minimum 100, 80 per effective day, maximum 500, with `result_limit=null`. |
| `prepare-spec --spec FILE --output FILE [--window-days DAYS]` | Validate and atomically materialize a spec, canonicalizing advertised platform aliases and anchoring absent times to the backend's latest complete `available_through`. |
| `create-run --spec FILE` | Create or reuse an idempotent run. |
| `get-run RUN_ID` | Poll status, stage, funnel, cutoff, watermark, and unresolved quality fields. |
| `resume RUN_ID` | Resume a recoverable original run after repairing its blocker. |
| `candidate-page RUN_ID --output FILE --offset OFFSET --limit 20` | Save one frozen candidate page with topic boundary, source item, contexts, and decision state. |
| `apply-classifications RUN_ID --page PAGE --file FILE [--repair]` | Locally validate and submit a complete page, or a non-empty repair subset. |
| `verify RUN_ID` | Gate final membership and evidence. |
| `export RUN_ID --format xlsx|jsonl` | Create an artifact only after verification. |
| `download RUN_ID ARTIFACT --output FILE` | Atomically save the returned artifact. |

Run from the Skill directory and copy this complete sequence. Keep the override command visible; execute it for every non-empty queue. Replace placeholders with returned or chosen values, and use the artifact name returned by `export`:

```bash
python3 scripts/topic_backend_client.py capabilities
# Add --window-days DAYS for an explicit whole-day relative range. Otherwise the advertised default applies.
# Complete explicit pairs pass unchanged. This atomically writes a distinct file and leaves the source unchanged:
python3 scripts/topic_backend_client.py prepare-spec --spec TOPIC_SPEC_PATH --output PREPARED_SPEC_PATH
python3 scripts/topic_backend_client.py create-run --spec PREPARED_SPEC_PATH
python3 scripts/topic_backend_client.py get-run RUN_ID
# Run only after repairing a pending-orphan, paused, or repairable failed run:
python3 scripts/topic_backend_client.py resume RUN_ID
# Start OFFSET at 0 and follow next_offset. Decide every page item before submitting it:
python3 scripts/topic_backend_client.py candidate-page RUN_ID --output CANDIDATE_PAGE_PATH --offset OFFSET --limit 20
python3 scripts/topic_backend_client.py apply-classifications RUN_ID --page CANDIDATE_PAGE_PATH --file DECISIONS_PATH
python3 scripts/topic_backend_client.py verify RUN_ID
python3 scripts/topic_backend_client.py export RUN_ID --format xlsx
python3 scripts/topic_backend_client.py download RUN_ID ARTIFACT_NAME --output OUTPUT_PATH
```

`source_freshness.ready` must be true. Its `available_through` is the exclusive safe endpoint shared by all recorded source channels; it is distinct from `source_generation_ms`, which records when ingestion completed. `prepare-spec` always fetches this value. Never substitute the Agent's system or local clock. For “最近 N 天”, omit both boundaries and pass `--window-days N`. A complete explicit pair is validated unchanged and is never silently clamped; a one-sided pair remains invalid.

`scope_filters.platforms` is the backend authority for platform spellings. `prepare-spec` consumes its canonical values and aliases before schema validation; never bypass the prepared file or manually substitute a different platform. Missing or malformed platform capabilities are a local configuration blocker. Unsupported or ambiguous values must be clarified or corrected before creating a run, never silently dropped from hard scope.

Never infer paging offsets from `total`: follow `next_offset`. Each decision file must cover exactly its page IDs once. `matched` requires non-empty evidence copied exactly from `item.text` or `context_items`; `not_matched` requires an empty evidence list. HTTP 200 may partially accept a batch. Preserve accepted siblings, repair only returned rejections, and use `next_pending_offset` until pending is zero.

The backend computes effective days from elapsed 24-hour intervals and freezes the candidate budget in the Run. Standard uses `min(500, max(100, effective_days * 80))`; seven days and longer remain capped at 500. Exhaustive retains a 5000-candidate safety limit. The caller classifies every frozen candidate but never supplies these controls.

Statuses include `pending`, `running`, `classification_ready`, `classification_in_progress`, `verification_ready`, `verified`, `failed`, and legacy v1 statuses. Resume applies only to recoverable pre-classification work; caller-owned classification states reject resume. The backend never chooses a semantic classifier for v2. The caller supplies decisions, never lease, concurrency, model-route, or retrieval controls. Client exits are `2` local validation/configuration, `3` service response, and `4` transport/invalid response.

A source-coverage rejection means the backend has no continuous successful-pull record for the requested channel and time interval and occurs before any run is created. An interval containing no feedback is valid only when the ingestion pipeline recorded that empty pull window. Ask the backend operator to pull/synchronize the missing interval, then repeat `create-run`; do not widen the topic, fabricate boundary feedback, or bypass verification. Each successful pull advances a backend-owned source generation, so a historical backfill creates a new immutable run even when the latest feedback timestamp is unchanged. New conversation-unit specs are rejected before run creation; do not invoke the general tagger or retry them. Historical conversation runs remain readable, but this Skill creates only feedback-unit runs.

Source snapshots, recall files, caller-decision audit data, and `final_reviewed.jsonl` are backend-internal and never available through artifact download. Only verified final deliverables `final_results.jsonl`, `quality_report.json`, and `feedback_list.xlsx` may be downloaded. Retrieval controls remain backend-owned.

Every run and exported artifact reports `mode`, `result_scope`, `matched_total`, `returned_feedback`, and `possibly_more_matches`. The only current `result_scope` values are `reviewed` and `representative`; copy the backend value and never invent `complete` or another value. Both modes export all verified matched rows, so `returned_feedback=matched_total`. When `retrieved_candidate_count > classified_count`, `possibly_more_matches=true in either mode` and `result_scope=representative`; otherwise the scope is `reviewed`. Representative means the delivery contains every confirmed match but may omit potential matches that were never classified. Never hide standard or exhaustive candidate safety-limit truncation.

## Excel delivery gate

Keep the downloaded `feedback_list.xlsx` byte-for-byte as a separate backend official Excel and present it first. Validate it before delivery:

```bash
python3 scripts/ensure_feedback_hyperlinks.py --official OFFICIAL_XLSX --input OFFICIAL_XLSX --check
```

Arbitrary post-processing—including filtering, sorting, grouping, labeling, annotations, added columns, or new sheets—does not exempt a workbook from link validation. Keep `Feedback ID` and a feedback-link column on every item-level row. Repair each derived workbook to a distinct output using only official ID-to-link mappings:

```bash
python3 scripts/ensure_feedback_hyperlinks.py --official OFFICIAL_XLSX --input DERIVED_XLSX --output VERIFIED_XLSX
```

Present only `VERIFIED_XLSX`, never the unverified derived input. The gate scans all item-level sheets, ignores summary-only sheets, and fails on missing columns, blank/duplicate/unknown IDs, missing official targets, or links that remain plain text. On failure, do not deliver that workbook. A derived workbook never replaces or hides the official artifact; if official export, download, or `--check` fails, disclose the blocker instead of substituting an Agent-generated file.
