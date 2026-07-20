# Review Policy

Review a queue item for every listed reason:

| Reason | Check |
| --- | --- |
| `classifier_requested_review` | Resolve an explicit classifier uncertainty. |
| `low_confidence_match` | Confirm the match boundary. |
| `vector_only_match` | Require source-grounded support beyond recall similarity. |
| `negative_query_conflict` | Decide which stated boundary applies. |
| `deterministic_label_sample` | Audit representative decisions for each label. |
| `high_confidence_reject_sample` | Check strong exclusions for systematic misses. |

Make every decision from source text, supplied conversation context, inclusion/exclusion criteria, and evidence. Do not decide from similarity or a score alone.

Submit a JSON list of overrides. Each entry needs `item_id`, `label` (`matched` or `not_matched`), `reason`, and `reviewer`; each item may appear once. An override changes only the run's decision layer and audit trail. It never edits a source record, source text, source link, or formal label.
