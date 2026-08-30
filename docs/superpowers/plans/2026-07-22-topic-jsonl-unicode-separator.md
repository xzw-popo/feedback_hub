# Topic JSONL Unicode Separator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent legal U+2028/U+2029 feedback text from corrupting topic-mining JSONL reads.

**Architecture:** JSONL writers continue preserving Unicode content. Both JSONL readers split records only on the format's LF delimiter and retain their existing validation behavior.

**Tech Stack:** Python 3, pytest, JSON Lines

## Global Constraints

- Do not mutate or sanitize source feedback text.
- Do not change the public error contract for malformed artifacts.
- Do not change unrelated topic-mining behavior.

---

### Task 1: Reproduce and fix Unicode separator parsing

**Files:**
- Modify: `feedback_hub/topic_mining/service.py`
- Test: `feedback_hub/tests/test_topic_mining_service.py`

**Interfaces:**
- Consumes: `_read_jsonl(path)` and `_read_required_jsonl(path, manifest)`.
- Produces: the same return values and errors, with LF-only record splitting.

- [x] **Step 1: Write failing regression tests**

Add parameterized coverage for U+2028 and U+2029 in ordinary and manifest-verified JSONL reads. Each test writes one object whose `text` contains the separator and asserts the original text is returned unchanged.

- [x] **Step 2: Verify the regression tests fail**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_service.py -k unicode_separator -q`

Expected: both reader-path tests fail with `RunVerificationError: invalid_artifact`.

- [x] **Step 3: Implement LF-only splitting**

Replace `splitlines()` in both JSONL readers with `split("\n")`. Keep empty-record skipping, JSON object validation, decoding, and exception mapping unchanged.

- [x] **Step 4: Verify focused and complete topic-mining tests**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_service.py -k 'unicode_separator or malformed_json' -q`

Expected: all selected tests pass.

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_service.py feedback_hub/tests/test_topic_mining_api.py feedback_hub/tests/test_topic_mining_export.py -q`

Expected: all tests pass.
