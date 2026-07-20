# Backend Contract

Use [`topic_backend_client.py`](../scripts/topic_backend_client.py). Configure `FEEDBACK_TOPIC_API_URL`; set `FEEDBACK_TOPIC_API_TOKEN` only when the service requires authorization. Do not place either value in a topic spec, artifact, or transcript.

| Command | Purpose |
| --- | --- |
| `capabilities` | Check supported schema, formats, read-only mode, and statuses. |
| `create-run --spec FILE` | Create or reuse an idempotent run. |
| `get-run RUN_ID` | Poll status, stage, funnel, cutoff, watermark, and unresolved quality fields. |
| `review-queue RUN_ID --output FILE` | Save the bounded review queue. |
| `apply-overrides RUN_ID --file FILE` | Submit a JSON override list. |
| `verify RUN_ID` | Gate final membership and evidence. |
| `export RUN_ID --format xlsx|jsonl` | Create an artifact only after verification. |
| `download RUN_ID ARTIFACT --output FILE` | Atomically save the returned artifact. |

Run from the Skill directory and copy this complete sequence. Keep the override command visible; execute it only when the queue needs decisions. Replace placeholders with returned or chosen values, and use the artifact name returned by `export`:

```bash
python3 scripts/topic_backend_client.py capabilities
python3 scripts/validate_topic_spec.py TOPIC_SPEC_PATH
python3 scripts/topic_backend_client.py create-run --spec TOPIC_SPEC_PATH
python3 scripts/topic_backend_client.py get-run RUN_ID
python3 scripts/topic_backend_client.py review-queue RUN_ID --output REVIEW_PATH
python3 scripts/topic_backend_client.py apply-overrides RUN_ID --file OVERRIDES_PATH
python3 scripts/topic_backend_client.py verify RUN_ID
python3 scripts/topic_backend_client.py export RUN_ID --format xlsx
python3 scripts/topic_backend_client.py download RUN_ID ARTIFACT_NAME --output OUTPUT_PATH
```

Statuses are `pending`, `running`, `review_ready`, `verified`, `failed`, and `paused_quota_exhausted`. A paused or failed run is inspectable, not exportable. Client exit categories are `2` local input/configuration error, `3` service response error, and `4` transport or invalid-response error; repair the reported category without exposing tokens.

The run may expose `source_snapshot.sqlite`, `scoped_items.jsonl`, `item_contexts.json`, `recall_candidates.jsonl`, `recall_manifest.json`, `classified.jsonl`, `classification_audit.jsonl`, `review_queue.jsonl`, `review_overrides.jsonl`, `final_reviewed.jsonl`, `final_results.jsonl`, `quality_report.json`, and `feedback_list.xlsx`. Treat only returned, verified artifacts as deliverables. Retrieval implementation and its controls are backend-owned; callers supply the topic boundary, never retrieval parameters.
