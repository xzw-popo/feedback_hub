# Topic Client Default URL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the packaged topic client connect to the current internal backend without mandatory environment setup while retaining explicit overrides.

**Architecture:** Keep URL ownership in the bundled Python client. Resolve CLI, environment, then packaged default; document the same contract in the Skill and backend reference.

**Tech Stack:** Python standard library, pytest, Markdown Skill package.

## Global Constraints

- Default origin: `http://charvelxia-any2.devcloud.woa.com:8000`.
- Precedence: `--base-url` > non-empty `FEEDBACK_TOPIC_API_URL` > packaged default.
- The current deployment requires company network or VPN and no API token.
- Preserve the client-appended `/api/topic-mining` path.

---

### Task 1: Package the internal backend default

**Files:**
- Modify: `codex-skills/mining-feedback-topics/scripts/topic_backend_client.py`
- Modify: `codex-skills/mining-feedback-topics/SKILL.md`
- Modify: `codex-skills/mining-feedback-topics/references/backend-contract.md`
- Test: `feedback_hub/tests/test_mining_feedback_topics_skill_package.py`

**Interfaces:**
- Consumes: optional `--base-url` and `FEEDBACK_TOPIC_API_URL`.
- Produces: `_base_url(value: str | None) -> str` with CLI/environment/default precedence.

- [ ] **Step 1: Write failing tests**

Add tests that unset `FEEDBACK_TOPIC_API_URL`, invoke `capabilities`, and assert the requested URL is `http://charvelxia-any2.devcloud.woa.com:8000/api/topic-mining/capabilities`. Add precedence and documentation assertions.

- [ ] **Step 2: Verify RED**

Run: `python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py -q`

Expected: the no-environment client test fails with `FEEDBACK_TOPIC_API_URL or --base-url is required`, and documentation assertions fail because the Skill still requires configuration.

- [ ] **Step 3: Implement the minimum behavior**

Add a packaged URL constant and resolve `value`, the environment variable, then the constant. Rewrite Skill and backend-contract configuration text to say the default works automatically and both overrides are optional.

- [ ] **Step 4: Verify GREEN**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py -q
python3 /Users/charvel/.codex/skills/.system/skill-creator/scripts/quick_validate.py codex-skills/mining-feedback-topics
git diff --check
```

Expected: tests pass, Skill is valid, and the diff check is clean.

- [ ] **Step 5: Commit**

```bash
git add codex-skills/mining-feedback-topics feedback_hub/tests/test_mining_feedback_topics_skill_package.py docs/superpowers/specs/2026-07-22-topic-client-default-url-design.md docs/superpowers/plans/2026-07-22-topic-client-default-url.md
git commit -m "fix: package topic backend default URL"
```
