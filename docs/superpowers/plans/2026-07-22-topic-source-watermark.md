# Topic Source Watermark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prepare default and rolling topic scopes from the backend's latest complete source-watermark time instead of Agent wall-clock time.

**Architecture:** The backend computes a stable common continuous coverage interval and advertises it in capabilities. The packaged client uses that interval to invoke the existing validator, and the Skill mandates this deterministic command for default and relative scopes.

**Tech Stack:** Python 3, FastAPI, SQLite, pytest, Codex Skill Markdown

## Global Constraints

- `available_through` is the exclusive safe time anchor; `source_generation_ms` remains a separate ingestion generation.
- The backend never silently changes a complete explicit time pair.
- Source data remains read-only.
- No Run is created when freshness metadata is unavailable.

---

### Task 1: Advertise backend source freshness

**Files:**
- Modify: `feedback_hub/topic_mining/source.py`
- Modify: `feedback_hub/topic_mining/config.py`
- Modify: `feedback_hub/topic_mining/api.py`
- Test: `feedback_hub/tests/test_topic_mining_source.py`
- Test: `feedback_hub/tests/test_topic_mining_api.py`

**Interfaces:**
- Produces: `source_freshness(path, observed_at_ms, sync_interval_seconds) -> dict[str, Any]`.
- Produces: `capabilities.source_freshness` with ready state, common interval, generation, lag, and cadence.

- [x] **Step 1: Add failing source and API tests**

Cover overlapping/adjacent interval merging, common multi-channel coverage, unavailable coverage, ISO timestamps, and the 1200-second cadence.

- [x] **Step 2: Run focused tests and confirm failure**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_source.py feedback_hub/tests/test_topic_mining_api.py -k 'freshness or capabilities' -q`

Expected: failures because freshness is not implemented or advertised.

- [x] **Step 3: Implement the source helper and capabilities field**

Merge each channel's intervals, take its latest segment, intersect those segments, and return `ready: false` when no common interval exists. Format UTC RFC3339 values in the API and keep millisecond values alongside them.

- [x] **Step 4: Run focused tests and confirm pass**

Run the Step 2 command. Expected: all selected tests pass.

### Task 2: Add waterline-aware Spec preparation

**Files:**
- Modify: `codex-skills/mining-feedback-topics/scripts/topic_backend_client.py`
- Test: `feedback_hub/tests/test_mining_feedback_topics_skill_package.py`

**Interfaces:**
- Produces: `prepare-spec --spec FILE --output FILE [--window-days DAYS]`.
- Consumes: `capabilities.source_freshness.available_through` and `default_time_days`.

- [x] **Step 1: Add failing client tests**

Prove missing-time specs end at the advertised waterline, complete explicit pairs remain unchanged, source specs remain unchanged, and unready/malformed freshness fails without creating a Run.

- [x] **Step 2: Run client tests and confirm failure**

Run: `python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py -k prepare_spec -q`

Expected: failures because the command does not exist.

- [x] **Step 3: Implement the command**

Fetch capabilities, validate the freshness contract, select explicit or default whole-day duration, and run the sibling validator as a captured subprocess with atomic output.

- [x] **Step 4: Run client tests and confirm pass**

Run the Step 2 command. Expected: all selected tests pass.

### Task 3: Update and validate the distributable Skill

**Files:**
- Modify: `codex-skills/mining-feedback-topics/SKILL.md`
- Modify: `codex-skills/mining-feedback-topics/references/backend-contract.md`
- Modify: `codex-skills/mining-feedback-topics/references/topic-spec.md`
- Test: `feedback_hub/tests/test_mining_feedback_topics_skill_package.py`

**Interfaces:**
- Consumes: the `prepare-spec` command and freshness fields from Tasks 1–2.
- Produces: a low-freedom workflow that never anchors rolling windows to local wall-clock time.

- [x] **Step 1: Add failing Skill-contract assertions**

Require `prepare-spec`, `available_through`, relative-window handling, and explicit-boundary preservation; reject the old `--default-now NOW` sequence.

- [x] **Step 2: Run Skill-contract tests and confirm failure**

Run: `python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py -k 'waterline or complete_verbatim_client_sequence' -q`

Expected: failures against the old Skill wording.

- [x] **Step 3: Update concise workflow and references**

Replace wall-clock preparation with the new command, document the freshness fields, and keep complete explicit pairs hard and unchanged.

- [x] **Step 4: Verify package and backend suites**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining*.py feedback_hub/tests/test_mining_feedback_topics_skill_package.py -q`

Expected: all selected tests pass.

- [ ] **Step 5: Validate and forward-test**

Run the skill validator and a fresh Agent preparation scenario. Expected: the prepared end equals advertised `available_through`, with no live Run creation.
