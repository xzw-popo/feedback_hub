# Topic Mining Vector Backend Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved remote vector backend, 20-minute incremental data pipeline, and bounded distributable topic-mining Skill in independently reviewable phases.

**Architecture:** Phase 1 creates a production vector service without changing remote scheduling. Phase 2 integrates raw pull, exact-ID dedupe, incremental embedding, deployment, and cron cutover. Phase 3 connects the service to the existing topic backend and applies the two-week/500/100 standard-mode policy.

**Tech Stack:** Python, SQLite, NumPy, Qwen3-Embedding-0.6B on PyTorch CPU, FastAPI, bash/cron, Codex Skill package.

## Global Constraints

- Execute phases in order; each phase must pass its acceptance gate before the next begins.
- Use test-driven development and the commit boundaries written in each phase plan.
- Do not deploy, restart services, build remote vectors, or alter cron without the explicit remote-write checkpoint in Phase 2.
- Preserve the user-owned untracked `WORKSPACE_GUIDE.md` unless the user explicitly resolves its ownership.
- Do not stage models, vector files, databases, raw feedback, logs, `.env`, or generated smoke artifacts.

---

## Spec Coverage Map

- Remote Qwen model, feedback-level vectors, SQLite metadata, immutable NumPy shards, atomic publication, model-version rebuild, compaction, and exact filtered search: Phase 1 Tasks 1–6.
- Every-20-minute scheduling, last-30-minute pull, exact-ID dedupe, pull-before-vector transaction order, coverage records, retry semantics, model deployment, and rollback: Phase 2 Tasks 1–5.
- Missing-time default of 14 days, standard/exhaustive intent, 500-candidate classification budget, 100-row representative output, RRF-only ordering, paged review, internal no-token use, and Skill guidance: Phase 3 Tasks 1–6.
- Failure injection, idempotency, quality comparison, current-scale performance, read-only smoke tests, and final regression evidence: the acceptance gates in all three phases.

## Ordered Phase Plans

- [ ] **Phase 1: Build and verify the vector index backend**

Plan: [Feedback Vector Index Backend Implementation Plan](2026-07-21-feedback-vector-index-backend.md)

Exit gate: the existing `HttpVectorSearchClient` passes against the new localhost service, incremental indexing is idempotent, and partial shards cannot become active.

- [ ] **Phase 2: Integrate ingestion, deploy, backfill, and cut over cron**

Plan: [Feedback Incremental Vector Pipeline Implementation Plan](2026-07-21-feedback-incremental-vector-pipeline.md)

Entry gate: Phase 1 suite passes. Exit gate: two overlapping 30-minute runs prove raw/vector idempotency, the initial backfill has zero pending rows, and exactly one `*/20` cron entry is active.

- [ ] **Phase 3: Apply bounded topic-mining and Skill behavior**

Plan: [Topic Mining Standard Mode Implementation Plan](2026-07-21-topic-mining-standard-mode.md)

Entry gate: Phase 2 vector health and watermark checks pass. Exit gate: no-time requests use 14 days, standard runs classify at most 500 and export at most 100, representative scope is disclosed, and the distributable Skill validates.

- [ ] **Final verification and PR update**

Run the complete runnable backend regression listed in Phase 3, repeat the vector and incremental suites, inspect `git diff --check`, verify no ignored data was staged, and update the existing PR with exact test evidence and remote rollout status.

Expected: code, docs, Skill, deployment behavior, and remote runtime all match the approved design; any intentionally deferred remote rollout is explicitly marked incomplete rather than implied complete.
