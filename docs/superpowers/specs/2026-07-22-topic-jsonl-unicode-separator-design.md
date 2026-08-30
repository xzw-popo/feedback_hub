# Topic JSONL Unicode Separator Design

## Problem

Topic runs write feedback as JSON Lines with `ensure_ascii=False`. A feedback body may therefore contain legal Unicode line-separator characters such as U+0085, U+2028, or U+2029. Python's `str.splitlines()` treats those characters as record boundaries, so it can split one JSON object inside a string and report `invalid_artifact`.

The first repair changed the two readers in `topic_mining/service.py`, but Export, review-queue API helpers, and classifier checkpoint/audit readers retained independent `splitlines()` implementations. A valid U+2028 in one recall item consequently passed verification but blocked both JSONL and XLSX export.

## Chosen approach

Create a small `feedback_hub/jsonl_io.py` module as the only authority for JSONL record boundaries used by topic mining and its user-authorized shared checkpoint scheduler. Keeping the helper at the package root lets `topic_discovery/model_routes.py` consume it without taking a reverse dependency on `topic_mining`.

It provides two layers:

- An LF-only record iterator that decodes UTF-8, splits only on `\n`, and skips empty records. Tolerant checkpoint/audit consumers use this layer so they can retain their existing per-record recovery behavior.
- A strict object loader that parses every record as a JSON object. Artifact, Export, and API readers use this layer and map its decoding, JSON, and shape errors into their existing domain-specific errors.

Writers continue using UTF-8 and `ensure_ascii=False`; source feedback is never sanitized or rewritten.

## Scope

Replace JSONL parsing in all production code under `feedback_hub/topic_mining`:

- ordinary and manifest-verified Service artifact readers;
- Export reads of `final_reviewed.jsonl` and `recall_candidates.jsonl`;
- review-queue API file/byte readers;
- classifier batch checkpoint, partial audit, and audit-history readers.

Also replace the JSONL checkpoint boundary in `feedback_hub/topic_discovery/model_routes.py`. This is a deliberately narrow shared-scheduler inclusion authorized after the topic-mining audit: its resumable checkpoint files carry topic-classifier model replies, and it must use the same LF-only boundary while retaining its existing malformed-record behavior.

Do not replace `splitlines()` where the input is intentionally a human-authored text format, configuration file, CLI output, or test assertion. Other non-topic packages remain outside this repair.

## Error behavior

- Service and Export continue returning `invalid_artifact` for malformed UTF-8, malformed JSON, and non-object records.
- Review API continues returning `invalid review queue artifact` through its existing HTTP mapping.
- Classifier checkpoint/audit recovery keeps its current tolerant-versus-strict behavior; only record-boundary detection changes.
- Truly malformed JSONL must still fail closed wherever it fails closed today.

## Verification

- Unit tests cover U+0085, U+2028, and U+2029 in the shared LF-only parser.
- Service regression tests continue covering ordinary and manifest-verified artifacts.
- Export tests reproduce the production failure from a separator inside a recall item and prove both JSONL and XLSX exports succeed without changing the text.
- Review-queue API tests prove separators survive both file and byte parsing paths.
- Classifier resume/audit tests prove separators inside stored model replies do not create phantom records.
- The real `run_pauseable_model_jobs` resume path proves a completed checkpoint row containing U+2028 in `raw_reply` resumes without reprocessing or text mutation.
- Existing malformed-JSON tests continue to fail with their original public errors.
- The complete topic-mining test suite passes.
