# Topic Spec

Build one JSON or YAML object with these required fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | Set to `1`. |
| `topic_name` | Short human-readable topic name. |
| `objective` | Decision or output the topic supports. |
| `scope` | Hard boundary: required `start_time`, required `end_time` (RFC3339 with timezone), and optional `platforms`, `products`, `channels`, `versions`. |
| `unit` | Set to `feedback`; only `feedback` is supported for new runs. |
| `mode` | Optional canonical run policy: `standard` (default) or `exhaustive`. Always emit the chosen canonical value. |
| `inclusion_criteria` | Observable facts required for a match. |
| `exclusion_criteria` | Adjacent concepts that must not match. |
| `positive_examples` | Representative matching phrases or cases. |
| `negative_examples` | Representative non-matching phrases or cases. |
| `lexical_hints` | `objects` and `contexts` terms for recall only. |
| `classification_labels` | Exactly `matched` and `not_matched`, each with `id` and `meaning`. |
| `output` | `preferred_format` (`xlsx` or `jsonl`) and a non-empty subset of the supported required fields: `feedback_text`, `feedback_time`, and `source_url`. The backend rejects every other field name. |

Treat scope as a hard metadata filter; treat criteria, examples, and hints as semantic guidance. Populate every hard-scope list only from values explicit in the current request; leave unstated lists empty unless one material question is required. Never infer scope from the workspace, examples, prior runs, or likely product context. When both time boundaries are absent, `prepare-spec` reads `default_time_days` and anchors the exact window to the backend's latest complete `source_freshness.available_through`; the current policy is 14 days. Never use the Agent's system or local current time. For an explicit whole-day relative range, omit both boundaries and add `--window-days DAYS`. When exactly one boundary is present, or a timezone cannot be resolved, ask one direct question for start, end, and timezone instead of inventing the missing value. A complete explicit pair passes unchanged and is never silently clamped.

Use `mode: standard` for ordinary requests, including long ranges such as six months. Its backend-owned classification budget scales with elapsed days from 100 to a maximum of 500; no budget field belongs in the spec. Use `mode: exhaustive` only when the user explicitly asks for all, complete, or exhaustive results. Both modes export every verified match, but candidate truncation still makes the result representative and must be disclosed. Exhaustive also obeys a backend safety limit.

Keep every target object and behavior named by the user as required inclusion conditions. Add paraphrases only when they preserve that meaning. Put unrequested adjacent objects or behaviors only in exclusion criteria; never broaden the target by adding them to hints or positive examples. Phrase criteria as facts a reviewer can check from source text and context.

For example, `语音输入结束后文字不上屏` can include “speech ends but recognized text is not inserted into the focused field” and exclude recognition errors, microphone failures, or ordinary clipboard use. Put those boundaries in criteria and examples, not in backend settings. Hints may contain terms such as “语音输入”, “上屏”, and “焦点”, but they only broaden recall.

Prepare a separate canonical JSON file before creating a run:

```bash
python3 scripts/topic_backend_client.py prepare-spec --spec TOPIC_SPEC_PATH --output PREPARED_SPEC_PATH
```

The validator leaves `TOPIC_SPEC_PATH` unchanged, atomically writes `PREPARED_SPEC_PATH`, and prints the same canonical JSON to stdout for compatibility. It rejects using the source path as output and removes temporary files after write errors. Pass only `PREPARED_SPEC_PATH` to `create-run`. Read the bundled [schema](topic-spec.schema.json) when exact structure is needed.
