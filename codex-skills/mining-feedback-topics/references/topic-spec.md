# Topic Spec

Build one JSON or YAML object with these required fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Set to `1`. |
| `topic_name` | Short human-readable topic name. |
| `objective` | Decision or output the topic supports. |
| `scope` | Hard boundary: required `start_time`, required `end_time` (RFC3339 with timezone), and optional `platforms`, `products`, `channels`, `versions`. Ask one time-range question when either required time is missing; never default to all history. |
| `unit` | `feedback` or `conversation`. |
| `inclusion_criteria` | Observable facts required for a match. |
| `exclusion_criteria` | Adjacent concepts that must not match. |
| `positive_examples` | Representative matching phrases or cases. |
| `negative_examples` | Representative non-matching phrases or cases. |
| `lexical_hints` | `objects` and `contexts` terms for recall only. |
| `classification_labels` | Exactly `matched` and `not_matched`, each with `id` and `meaning`. |
| `output` | `preferred_format` (`xlsx` or `jsonl`) and a non-empty subset of the supported required fields: `feedback_text`, `feedback_time`, and `source_url`. The backend rejects every other field name. |

Treat scope as a hard metadata filter; treat criteria, examples, and hints as semantic guidance. Populate every hard-scope list only from values explicit in the current request; leave unstated lists empty unless one material question is required. Never infer scope from the workspace, examples, prior runs, or likely product context. Keep every target object and behavior named by the user as required inclusion conditions. Add paraphrases only when they preserve that meaning. Put unrequested adjacent objects or behaviors only in exclusion criteria; never broaden the target by adding them to hints or positive examples. Phrase criteria as facts a reviewer can check from source text and context.

For example, `语音输入结束后文字不上屏` can include “speech ends but recognized text is not inserted into the focused field” and exclude recognition errors, microphone failures, or ordinary clipboard use. Put those boundaries in criteria and examples, not in backend settings. Hints may contain terms such as “语音输入”, “上屏”, and “焦点”, but they only broaden recall.

Validate the completed file with [`validate_topic_spec.py`](../scripts/validate_topic_spec.py) before creating a run. Read the bundled [schema](topic-spec.schema.json) when exact structure is needed.
