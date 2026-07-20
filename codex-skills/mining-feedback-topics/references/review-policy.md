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

Make every decision from `source_item`, supplied `context_items`, inclusion/exclusion criteria, and evidence. Do not decide from similarity or a score alone. Submit one explicit decision for every queue item, including a confirmation when the label stays unchanged; verification remains blocked until queue and decision IDs match exactly.

Submit a JSON list of review decisions. Each entry needs `item_id`, `label` (`matched` or `not_matched`), `reason`, and `reviewer`; each item may appear once. When setting `matched` without existing grounded evidence—especially changing `not_matched` to `matched`—also provide `evidence` as a non-empty string list. Every evidence string must be an exact substring of `source_item.text` or one of the supplied `context_items` texts. A decision changes only the run's decision layer and audit trail. It never edits a source record, source text, source link, or formal label.
