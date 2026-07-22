# Backend Contract

Use [`topic_backend_client.py`](../scripts/topic_backend_client.py). Configure `FEEDBACK_TOPIC_API_URL`. The current internal-network/VPN deployment advertises `authentication=internal_network_boundary`, so `FEEDBACK_TOPIC_API_TOKEN` is unused; preserve the optional client support only for a future deployment that explicitly requires authorization. Do not place either value in a topic spec, artifact, or transcript.

| Command | Purpose |
| --- | --- |
| `capabilities` | Check supported schema, formats, read-only mode, statuses, `default_time_days`, `run_modes`, and authentication policy. Current policy: 14 days; standard candidate/result limits 500/100; exhaustive candidate safety limit 5000 and no ordinary result limit. |
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
# Materialize the advertised default only when both times are absent; complete pairs pass unchanged.
# This atomically writes a distinct canonical JSON file and leaves TOPIC_SPEC_PATH unchanged:
python3 scripts/validate_topic_spec.py --default-now NOW --default-days 14 --output PREPARED_SPEC_PATH TOPIC_SPEC_PATH
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

Never infer paging offsets from `total`: follow the returned `next_offset` and stop only when it is null. `OVERRIDES_PATH` must contain one explicit decision for every item across all pages, even when confirming the existing label. Submit only after merging the complete queue. A decision that sets an evidence-free item to `matched` must include a non-empty `evidence` list whose strings are exact substrings of the returned `source_item.text` or `context_items` text. Partial or extra decision IDs keep verification blocked.

Statuses are `pending`, `running`, `review_ready`, `verified`, `failed`, and `paused_quota_exhausted`. A paused or failed run is inspectable, not exportable. After `paused_quota_exhausted`, repair quota or model-service access and run `resume RUN_ID` on the original run. After another repairable `failed`, fix the reported service or configuration failure and resume the original run. Do not create a replacement run for a run that already exists in the same source generation. Pending orphan runs can resume immediately; running runs resume only after the backend-owned worker lease is stale. `review_ready` and `verified` reject resume. The caller never supplies lease, concurrency, route, model, or retrieval controls. Client exit categories are `2` local input/configuration error, `3` service response error, and `4` transport or invalid-response error; repair the reported category without exposing tokens.

A source-coverage rejection means the backend has no continuous successful-pull record for the requested channel and time interval and occurs before any run is created. An interval containing no feedback is valid only when the ingestion pipeline recorded that empty pull window. Ask the backend operator to pull/synchronize the missing interval, then repeat `create-run`; do not widen the topic, fabricate boundary feedback, or bypass verification. Each successful pull advances a backend-owned source generation, so a historical backfill creates a new immutable run even when the latest feedback timestamp is unchanged. `source_readiness_error` for conversation units means source conversation IDs still contain ingestion placeholders; do not invoke the general tagger from this Skill. Use a feedback-unit topic when it preserves the user's intent, otherwise report the backend preparation blocker.

Source snapshots, scoped rows, recall/classification files, audits, review files, and `final_reviewed.jsonl` are backend-internal and never available through artifact download. Only verified final deliverables that actually exist may be downloaded: `final_results.jsonl`, `quality_report.json`, and `feedback_list.xlsx`. Retrieval implementation and its controls are backend-owned; callers supply the topic boundary, never retrieval parameters.

Every run and exported artifact reports `mode`, `result_scope`, `matched_total`, `returned_feedback`, and `possibly_more_matches`. The only current `result_scope` values are `reviewed` and `representative`; copy the backend value and never invent `complete` or another value. In standard mode, classification is capped at 500 candidates and export at 100 confirmed rows. When `retrieved_candidate_count > classified_count`, `possibly_more_matches=true in either mode`; standard also sets it when confirmed matches exceed the export limit. `result_scope=representative` or `possibly_more_matches=true` means the delivery is not a complete list. Exhaustive mode removes the ordinary 100-row export cap but still reports candidate safety-limit truncation; never hide that limitation.
