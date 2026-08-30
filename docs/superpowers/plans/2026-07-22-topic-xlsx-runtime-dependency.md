# Topic XLSX Runtime Dependency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure every normal API deployment installs the XLSX export dependency.

**Architecture:** Move the pinned `openpyxl` dependency into the base runtime manifest consumed by `devcloud_runtime.sh`. Keep the topic-specific manifest as an include-only compatibility entry point and guard the relationship with a static test.

**Tech Stack:** Python, pytest, pip requirements files

## Global Constraints

- Pin `openpyxl` to `3.1.5`.
- Do not change workbook contents or export APIs.
- Avoid server-only manual dependency state as the final fix.

---

### Task 1: Correct runtime dependency ownership

**Files:**
- Create: `feedback_hub/tests/test_topic_mining_runtime_requirements.py`
- Modify: `requirements.txt`
- Modify: `requirements-topic-mining.txt`

**Interfaces:**
- Consumes: `scripts/devcloud_runtime.sh`, which installs `requirements.txt`.
- Produces: a base API environment containing `openpyxl==3.1.5`.

- [x] **Step 1: Write the failing packaging test**

Assert that `requirements.txt` contains exactly one `openpyxl==3.1.5` entry and that `requirements-topic-mining.txt` contains only `-r requirements.txt`.

- [x] **Step 2: Run the test and confirm failure**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_runtime_requirements.py -q`

Expected: failure because `openpyxl` is absent from the base requirements and duplicated in the topic manifest.

- [x] **Step 3: Move the dependency declaration**

Add `openpyxl==3.1.5` to `requirements.txt` and remove it from `requirements-topic-mining.txt`.

- [x] **Step 4: Verify packaging and export behavior**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_runtime_requirements.py feedback_hub/tests/test_topic_mining_export.py feedback_hub/tests/test_topic_mining_api.py -q`

Expected: all selected tests pass.
