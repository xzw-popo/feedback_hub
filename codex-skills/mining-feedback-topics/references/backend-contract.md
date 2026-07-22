# Backend Contract

Use [`topic_backend_client.py`](../scripts/topic_backend_client.py). The client defaults to `http://charvelxia-any2.devcloud.woa.com:8000`. Both URL overrides are optional: pass `--base-url` or set non-empty `FEEDBACK_TOPIC_API_URL`. The current internal-network/VPN deployment advertises `authentication=internal_network_boundary`, so `FEEDBACK_TOPIC_API_TOKEN` is unused; preserve the optional client support only for a future deployment that explicitly requires authorization. Do not place either value in a topic spec, artifact, or transcript.

| Command | Purpose |
| --- | --- |
| `capabilities` | Check supported schema, formats, read-only mode, statuses, `supported_units=["feedback"]`, `default_time_days`, `run_modes`, authentication policy, and `source_freshness`. Current standard policy: minimum 100 candidates, 80 per effective day, maximum 500; review QA samples 20 per effective day with maximum 80; `result_limit=null`. Exhaustive retains a 5000-candidate safety limit. |
| `prepare-spec --spec FILE --output FILE [--window-days DAYS]` | Validate and atomically materialize a spec, anchoring absent times to the backend's latest complete `available_through`. |
| `create-run --spec FILE` | Create or reuse an idempotent run. |
| `get-run RUN_ID` | Poll status, stage, funnel, cutoff, watermark, and unresolved quality fields. |
| `resume RUN_ID` | Resume a recoverable original run after repairing its blocker. |
| `review-queue RUN_ID --output FILE --offset OFFSET --limit 50` | Save one review page and return `total` plus `next_offset`. |
| `apply-overrides RUN_ID --file FILE` | Submit a JSON override list. |
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
# Start OFFSET at 0; repeat with OFFSET=next_offset until next_offset is null:
python3 scripts/topic_backend_client.py review-queue RUN_ID --output REVIEW_PAGE_PATH --offset OFFSET --limit 50
# Merge one decision for every item from every page into OVERRIDES_PATH, then submit once:
python3 scripts/topic_backend_client.py apply-overrides RUN_ID --file OVERRIDES_PATH
python3 scripts/topic_backend_client.py verify RUN_ID
python3 scripts/topic_backend_client.py export RUN_ID --format xlsx
python3 scripts/topic_backend_client.py download RUN_ID ARTIFACT_NAME --output OUTPUT_PATH
```

`source_freshness.ready` must be true. Its `available_through` is the exclusive safe endpoint shared by all recorded source channels; it is distinct from `source_generation_ms`, which records when ingestion completed. `prepare-spec` always fetches this value. Never substitute the Agent's system or local clock. For “最近 N 天”, omit both boundaries and pass `--window-days N`. A complete explicit pair is validated unchanged and is never silently clamped; a one-sided pair remains invalid.

Never infer paging offsets from `total`: follow the returned `next_offset` and stop only when it is null. `OVERRIDES_PATH` must contain one explicit decision for every item across all pages, even when confirming the existing label. Submit only after merging the complete queue. A decision that sets an evidence-free item to `matched` must include a non-empty `evidence` list whose strings are exact substrings of the returned `source_item.text` or `context_items` text. Partial or extra decision IDs keep verification blocked.

The backend computes effective days from elapsed 24-hour intervals and freezes both budgets in the Run. Standard classification uses `min(500, max(100, effective_days * 80))`; seven days and longer remain capped at 500. Mandatory review includes classifier-requested and low-confidence items and is never capped. Vector-only, negative-conflict, matched, and high-confidence-reject QA are deterministically sampled at 20 per effective day, capped at 80. The caller reviews every returned queue item but never supplies these controls.

Statuses are `pending`, `running`, `review_ready`, `verified`, `failed`, and `paused_quota_exhausted`. A paused or failed run is inspectable, not exportable. After `paused_quota_exhausted`, repair quota or model-service access and run `resume RUN_ID` on the original run. After another repairable `failed`, fix the reported service or configuration failure and resume the original run. Do not create a replacement run for a run that already exists in the same source generation. Pending orphan runs can resume immediately; running runs resume only after the backend-owned worker lease is stale. `review_ready` and `verified` reject resume. The caller never supplies lease, concurrency, route, model, or retrieval controls. Client exit categories are `2` local input/configuration error, `3` service response error, and `4` transport or invalid-response error; repair the reported category without exposing tokens.

A source-coverage rejection means the backend has no continuous successful-pull record for the requested channel and time interval and occurs before any run is created. An interval containing no feedback is valid only when the ingestion pipeline recorded that empty pull window. Ask the backend operator to pull/synchronize the missing interval, then repeat `create-run`; do not widen the topic, fabricate boundary feedback, or bypass verification. Each successful pull advances a backend-owned source generation, so a historical backfill creates a new immutable run even when the latest feedback timestamp is unchanged. New conversation-unit specs are rejected before run creation; do not invoke the general tagger or retry them. Historical conversation runs remain readable, but this Skill creates only feedback-unit runs.

Source snapshots, scoped rows, recall/classification files, audits, review files, and `final_reviewed.jsonl` are backend-internal and never available through artifact download. Only verified final deliverables that actually exist may be downloaded: `final_results.jsonl`, `quality_report.json`, and `feedback_list.xlsx`. Retrieval implementation and its controls are backend-owned; callers supply the topic boundary, never retrieval parameters.

Every run and exported artifact reports `mode`, `result_scope`, `matched_total`, `returned_feedback`, and `possibly_more_matches`. The only current `result_scope` values are `reviewed` and `representative`; copy the backend value and never invent `complete` or another value. Both modes export all verified matched rows, so `returned_feedback=matched_total`. When `retrieved_candidate_count > classified_count`, `possibly_more_matches=true in either mode` and `result_scope=representative`; otherwise the scope is `reviewed`. Representative means the delivery contains every confirmed match but may omit potential matches that were never classified. Never hide standard or exhaustive candidate safety-limit truncation.
