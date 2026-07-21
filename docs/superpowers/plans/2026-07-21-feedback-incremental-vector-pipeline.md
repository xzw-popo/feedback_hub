# Feedback Incremental Vector Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the remote two-hour pull with a locked 20-minute pipeline that pulls the last 30 minutes, commits exact-ID dedupe, and then incrementally vectorizes every unindexed feedback row.

**Architecture:** A Python orchestrator owns the pull-to-vector order and emits one structured run result; a thin shell wrapper supplies logging for cron. Pull and source coverage commit before vector work starts, so vector failure is retryable and cannot discard raw feedback. Deployment keeps model and index data under the preserved `feedback_hub/data/` directory.

**Tech Stack:** Python 3.11, existing OpenAPI puller, SQLite, POSIX file locking, bash, cron, SSH/DevCloud, pytest.

## Global Constraints

- Schedule is exactly every 20 minutes; pull window is exactly the most recent 30 minutes.
- The 10-minute overlap is intentional and must remain safe through `feedback_id` idempotency.
- Pull success and `feedback_source_coverage` publish in one transaction before vectorization.
- The incremental pipeline must not invoke generic tagging or write formal feedback labels.
- Vectorization failure leaves raw rows and coverage committed, returns non-zero, and retries missing vectors next run.
- Only one incremental pipeline may write at a time; a second invocation exits as `skipped_locked` without waiting.
- Remote model, database, vector files, `.env`, and logs remain outside git and survive code deployment.
- Production cron changes and first backfill require an explicit execution-time approval checkpoint.

---

## File Structure

- Create `feedback_hub/incremental_pipeline.py`: Python transaction order, lock, run result, and failure semantics.
- Modify `feedback_hub/cli.py`: add `pipeline incremental` and `ingest pull` while preserving `pull` compatibility.
- Create `scripts/feedback_incremental_sync.sh`: cron-safe wrapper and structured logging.
- Create `scripts/install_feedback_incremental_cron.sh`: idempotent cron installer and backup.
- Create `scripts/deploy_vector_backend_devcloud.sh`: one-time dependency/model/index bootstrap.
- Modify `scripts/devcloud_runtime.sh`: coordinate app status with vector-service health.
- Modify `deploy_devcloud.sh`: restart the vector process after code replacement when configured.
- Modify `docs/devcloud-container-deployment.md`: exact setup, health checks, rollback, and cron commands.
- Modify `WORKSPACE_GUIDE.md` only after confirming the currently untracked workspace-owned file is intended to be updated and tracked.
- Add tests in `feedback_hub/tests/test_incremental_pipeline.py`, `test_feedback_incremental_sync_script.py`, `test_install_feedback_incremental_cron.py`, and `test_vector_backend_deploy.py`.

### Task 1: Pull-Then-Vector Orchestrator

**Files:**
- Create: `feedback_hub/incremental_pipeline.py`
- Test: `feedback_hub/tests/test_incremental_pipeline.py`

**Interfaces:**
- Consumes: `puller.pull(start, end, conn=conn)` and `vector_index.sync.sync_pending(config)`.
- Produces: `run_incremental(*, now, pull_window, pull_fn=None, vector_sync_fn=None) -> IncrementalRunResult` and `backfill_coverage(*, start, end, chunk, pull_fn=None) -> BackfillResult`.

- [ ] **Step 1: Write failing order, idempotency, and partial-failure tests**

```python
def test_pipeline_commits_pull_before_vector_sync(tmp_path):
    observed = []
    def pull_fn(start, end, *, conn):
        insert_feedback(conn, "f1")
        conn.commit()
        observed.append("pull_committed")
        return {"inserted_count": 1, "skipped_dup_count": 0, "source_generation_ms": 100}
    def vector_fn():
        assert scalar(tmp_path / "feedback.db", "SELECT COUNT(*) FROM feedback") == 1
        observed.append("vector")
        return SyncResult("v1", 1, 0, 100)
    result = run_incremental(now=fixed_now(), pull_window=timedelta(minutes=30), pull_fn=pull_fn, vector_sync_fn=vector_fn)
    assert observed == ["pull_committed", "vector"]
    assert result.status == "succeeded"

def test_vector_failure_preserves_pull_and_returns_partial(tmp_path):
    with pytest.raises(IncrementalPipelineError) as error:
        run_incremental(now=fixed_now(), pull_window=timedelta(minutes=30), pull_fn=successful_pull, vector_sync_fn=failing_vector_sync)
    assert error.value.result.pull_status == "succeeded"
    assert error.value.result.vector_status == "failed"

def test_backfill_commits_each_six_hour_window_and_resumes_after_failure(tmp_path):
    failing = PullSequence(fail_on_call=2)
    with pytest.raises(RuntimeError, match="fixture pull failure"):
        backfill_coverage(start=fixed_start(), end=fixed_start() + timedelta(hours=18), chunk=timedelta(hours=6), pull_fn=failing)
    resumed = backfill_coverage(start=fixed_start(), end=fixed_start() + timedelta(hours=18), chunk=timedelta(hours=6), pull_fn=failing.without_failure())
    assert resumed.skipped_covered_windows == 1
    assert resumed.completed_windows == 2
```

- [ ] **Step 2: Run the pipeline tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_incremental_pipeline.py -q`

Expected: FAIL because `feedback_hub.incremental_pipeline` does not exist.

- [ ] **Step 3: Implement the immutable run result and non-blocking lock**

```python
@dataclass(frozen=True)
class IncrementalRunResult:
    status: str
    started_at: str
    finished_at: str
    window_start: str
    window_end: str
    pull_status: str
    vector_status: str
    fetched_count: int
    inserted_count: int
    skipped_dup_count: int
    vectorized_count: int
    source_generation_ms: int | None
    vector_watermark_ms: int | None
    error_code: str

@contextmanager
def nonblocking_lock(path: Path):
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise PipelineAlreadyRunning("incremental pipeline already running") from None
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
```

- [ ] **Step 4: Implement exact execution order and error result**

Open/init the source database, call `puller.pull()` for `[now-30m, now]`, close that connection, then call `sync_pending()`. Catch vector exceptions only after constructing a `partial` result; raise `IncrementalPipelineError(result)` so the CLI can emit JSON and exit non-zero. Do not call `tagger`, `pipeline.run_tagging`, or any label writer.

Persist the same result in `embedding_sync_run` with `run_type=incremental_pipeline`, exact window timestamps, fetched/inserted/duplicate/vectorized/failed counts, and the terminal status. The persisted row and emitted JSON must use the same run ID and counters.

`backfill_coverage()` must split `[start, end]` into adjacent half-open windows, default to six-hour chunks, skip a chunk only when `feedback_source_coverage` already continuously covers that exact channel/window, and call the same `puller.pull()` for every missing chunk. Each successful chunk commits independently so a later network failure is resumable and exact-ID dedupe remains authoritative.

- [ ] **Step 5: Run pipeline and puller regression tests**

Run: `python3 -m pytest feedback_hub/tests/test_incremental_pipeline.py feedback_hub/tests/test_puller.py -q`

Expected: all tests PASS, including the existing atomic pull-plus-coverage tests.

- [ ] **Step 6: Commit the orchestrator**

```bash
git add feedback_hub/incremental_pipeline.py feedback_hub/tests/test_incremental_pipeline.py
git commit -m "feat: add pull then vector incremental pipeline"
```

### Task 2: CLI and Cron-Safe Wrapper

**Files:**
- Modify: `feedback_hub/cli.py`
- Create: `scripts/feedback_incremental_sync.sh`
- Test: `feedback_hub/tests/test_cli.py`
- Test: `feedback_hub/tests/test_feedback_incremental_sync_script.py`

**Interfaces:**
- Produces: `python -m feedback_hub.cli pipeline incremental --pull-window 30m`, resumable `ingest backfill --last DURATION --chunk 6h`, and backward-compatible `pull`.
- Consumes: `run_incremental()` from Task 1 and vector commands from the vector-backend plan.

- [ ] **Step 1: Write failing parser and shell-contract tests**

```python
def test_incremental_cli_defaults_to_thirty_minutes(monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_incremental", lambda **kwargs: successful_result(window_minutes=30))
    assert cli.main(["pipeline", "incremental"]) == 0
    assert json.loads(capsys.readouterr().out)["window_minutes"] == 30

def test_incremental_script_never_runs_tagging():
    body = SCRIPT.read_text(encoding="utf-8")
    assert "pipeline incremental --pull-window 30m" in body
    assert " feedback_hub.cli tag" not in body
```

- [ ] **Step 2: Run CLI/wrapper tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_cli.py feedback_hub/tests/test_feedback_incremental_sync_script.py -q`

Expected: new tests FAIL because the command and script are missing.

- [ ] **Step 3: Add nested pipeline and ingest command groups**

```python
pipeline_parser = sub.add_parser("pipeline")
pipeline_sub = pipeline_parser.add_subparsers(dest="pipeline_command", required=True)
incremental = pipeline_sub.add_parser("incremental")
incremental.add_argument("--pull-window", default="30m")
incremental.set_defaults(func=cmd_pipeline_incremental)

ingest_parser = sub.add_parser("ingest")
ingest_sub = ingest_parser.add_subparsers(dest="ingest_command", required=True)
ingest_pull = ingest_sub.add_parser("pull")
add_pull_window_arguments(ingest_pull)
ingest_pull.set_defaults(func=cmd_pull)
ingest_backfill = ingest_sub.add_parser("backfill")
ingest_backfill.add_argument("--last", required=True)
ingest_backfill.add_argument("--chunk", default="6h")
ingest_backfill.set_defaults(func=cmd_ingest_backfill)
```

Refactor the existing `pull` argument registration into `add_pull_window_arguments()` so both spellings share identical behavior. `cmd_pipeline_incremental()` prints one JSON object even for partial failure and returns exit code 1; lock contention returns exit code 75 with `status=skipped_locked`.

- [ ] **Step 4: Create the shell wrapper**

```bash
#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-${PROJECT_DIR}/.venv/bin/python}"
LOG_DIR="${LOG_DIR:-${PROJECT_DIR}/feedback_hub/data/logs}"
mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"
"$PYTHON_BIN" -m feedback_hub.cli pipeline incremental --pull-window 30m \
  >> "${LOG_DIR}/feedback_incremental_sync.log" 2>&1
```

The Python lock is authoritative; the shell script must not create a second lock implementation.

- [ ] **Step 5: Run CLI/wrapper tests**

Run: `python3 -m pytest feedback_hub/tests/test_cli.py feedback_hub/tests/test_feedback_incremental_sync_script.py -q`

Expected: all tests PASS.

- [ ] **Step 6: Commit CLI and wrapper**

```bash
git add feedback_hub/cli.py scripts/feedback_incremental_sync.sh feedback_hub/tests/test_cli.py feedback_hub/tests/test_feedback_incremental_sync_script.py
git commit -m "feat: expose incremental feedback sync command"
```

### Task 3: Idempotent 20-Minute Cron Installer

**Files:**
- Create: `scripts/install_feedback_incremental_cron.sh`
- Test: `feedback_hub/tests/test_install_feedback_incremental_cron.py`

**Interfaces:**
- Produces one root crontab entry invoking `scripts/feedback_incremental_sync.sh` at `*/20 * * * *`.
- Replaces the legacy `feedback_daily_sync.sh --last 150m --no-sync` entry and preserves unrelated cron jobs.

- [ ] **Step 1: Write failing installer fixture tests**

```python
def test_installer_replaces_legacy_entry_and_preserves_unrelated_jobs(tmp_path):
    existing = "0 */2 * * * cd /opt/feedback_hub && scripts/feedback_daily_sync.sh --last 150m --no-sync\n15 4 * * * /opt/other.sh\n"
    result = run_installer(tmp_path, existing_crontab=existing)
    assert result.count("feedback_incremental_sync.sh") == 1
    assert "*/20 * * * *" in result
    assert "feedback_daily_sync.sh --last 150m" not in result
    assert "/opt/other.sh" in result
```

- [ ] **Step 2: Run installer tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_install_feedback_incremental_cron.py -q`

Expected: FAIL because the installer is missing.

- [ ] **Step 3: Implement backup, filter, append, and verification**

The script accepts `--project-dir` and `--dry-run`, writes the previous crontab to `feedback_hub/data/cron-backups/crontab-YYYYmmdd-HHMMSS.txt`, removes only lines containing the legacy or new feedback sync script, appends exactly:

```cron
*/20 * * * * cd /opt/feedback_hub && PYTHON_BIN=./.venv/bin/python scripts/feedback_incremental_sync.sh
```

After installation, read `crontab -l` back and require exactly one matching line.

- [ ] **Step 4: Run installer tests**

Run: `python3 -m pytest feedback_hub/tests/test_install_feedback_incremental_cron.py -q`

Expected: all tests PASS.

- [ ] **Step 5: Commit the installer**

```bash
git add scripts/install_feedback_incremental_cron.sh feedback_hub/tests/test_install_feedback_incremental_cron.py
git commit -m "ops: install twenty minute feedback sync cron"
```

### Task 4: DevCloud Model and Runtime Deployment

**Files:**
- Create: `scripts/deploy_vector_backend_devcloud.sh`
- Modify: `scripts/devcloud_runtime.sh`
- Modify: `deploy_devcloud.sh`
- Modify: `docs/devcloud-container-deployment.md`
- Conditionally modify: `WORKSPACE_GUIDE.md`
- Test: `feedback_hub/tests/test_vector_backend_deploy.py`
- Test: `feedback_hub/tests/test_deploy_devcloud.py`

**Interfaces:**
- Consumes: vector runtime and CLI from the vector-backend plan.
- Produces: repeatable remote dependency/model install, vector health checks, and code-deploy restart behavior; production backfill stays in Task 5.

- [ ] **Step 1: Write failing deployment-script tests**

```python
def test_vector_deploy_preserves_model_and_index_under_data():
    body = DEPLOY.read_text(encoding="utf-8")
    assert "feedback_hub/data/models/Qwen3-Embedding-0.6B" in body
    assert "feedback_hub/data/vector_index" in body
    assert "requirements-vector.txt" in body
    assert "--start-after-bootstrap" in body
    assert "ingest backfill" not in body

def test_regular_deploy_restarts_vector_after_directory_swap():
    body = DEPLOY_DEV_CLOUD.read_text(encoding="utf-8")
    assert body.index("mv \"\\${NEW}\" \"\\${OLD}\"") < body.index("scripts/vector_runtime.sh restart")
```

- [ ] **Step 2: Run deployment tests and verify failure**

Run: `python3 -m pytest feedback_hub/tests/test_vector_backend_deploy.py feedback_hub/tests/test_deploy_devcloud.py -q`

Expected: new tests FAIL because the vector bootstrap path is absent.

- [ ] **Step 3: Implement one-time model bootstrap**

`scripts/deploy_vector_backend_devcloud.sh` must:

1. Validate the local source model contains `config.json`, `tokenizer.json`, and `model.safetensors`.
2. Upload it only when the remote checksum manifest differs.
3. Install `torch>=2.3,<3` from `https://download.pytorch.org/whl/cpu`, install `requirements-vector.txt` into `/opt/feedback_hub/.venv`, and assert `torch.version.cuda is None`.
4. Leave historical raw backfill and vector rebuild to the separately approved rollout steps; the deployment script must not silently pull production data.
5. Support a `--start-after-bootstrap` flag that starts `scripts/vector_runtime.sh` only after an active manifest already exists.

Default `MODEL_SOURCE_DIR` to the verified local experiment model directory but allow an explicit path override. Never add the model to the deployment tarball or git.

- [ ] **Step 4: Make normal code deployment vector-aware**

After the atomic remote directory swap, `deploy_devcloud.sh` must restart `scripts/vector_runtime.sh` when an active manifest exists, then restart `scripts/devcloud_runtime.sh`. On vector restart failure, leave the app stopped and return non-zero rather than serving topic mining against a stale or absent vector process.

- [ ] **Step 5: Update the deployment runbook**

Document exact model path, vector port, environment variables, initial rebuild command, health checks, cron install, log locations, rollback to the previous manifest, and restoration of the crontab backup. Document that `TOPIC_MINING_API_TOKEN` remains unset because access is restricted to internal network/VPN.

Before modifying `WORKSPACE_GUIDE.md`, run `git status --short WORKSPACE_GUIDE.md`. If it remains an untracked user-owned file, stop and request user direction instead of overwriting or staging it. If the user authorizes the update, add the new vector runtime, incremental sync script, model/index data paths, and cron ownership to its directory map.

- [ ] **Step 6: Run deployment and documentation tests**

Run: `python3 -m pytest feedback_hub/tests/test_vector_backend_deploy.py feedback_hub/tests/test_deploy_devcloud.py -q`

Expected: all tests PASS.

- [ ] **Step 7: Commit deployment support**

```bash
git add scripts/deploy_vector_backend_devcloud.sh scripts/devcloud_runtime.sh deploy_devcloud.sh docs/devcloud-container-deployment.md feedback_hub/tests/test_vector_backend_deploy.py feedback_hub/tests/test_deploy_devcloud.py
git commit -m "ops: deploy feedback vector backend"
```

Do not stage `WORKSPACE_GUIDE.md` unless the ownership checkpoint in Step 5 was explicitly resolved.

### Task 5: Staged Remote Rollout and Cron Cutover

**Files:**
- No repository source files; produces remote logs, index data, and crontab backup under ignored data paths.

**Interfaces:**
- Consumes all earlier tasks and the approved DevCloud host `root@charvelxia-any2.devcloud.woa.com:36000`.
- Produces a healthy full backfill, one successful manual incremental run, and the installed 20-minute cron.

- [ ] **Step 1: Obtain explicit approval for remote writes**

State the exact actions: upload model dependencies, build derived vector files, restart internal services, back up root crontab, and replace the legacy feedback sync entry. Do not continue without approval.

- [ ] **Step 2: Back up current remote state**

Run:

```bash
ssh -p 36000 root@charvelxia-any2.devcloud.woa.com 'cd /opt/feedback_hub && cp feedback_hub/data/feedback.db feedback_hub/data/feedback.db.pre-vector && crontab -l > feedback_hub/data/cron-backups/crontab-pre-vector.txt'
```

Expected: both backup files exist and have non-zero size.

- [ ] **Step 3: Deploy code dependencies and the model without pulling data**

Run: `MODEL_SOURCE_DIR=/Users/charvel/Desktop/用户反馈_2026_0612/feedback_hub/data/embedding_lab/models/Qwen3-Embedding-0.6B scripts/deploy_vector_backend_devcloud.sh`

Expected: dependency and model checksum verification succeeds; no cron entry has changed and no production pull has run yet.

- [ ] **Step 4: Backfill the default two-week source coverage without tagging**

Run:

```bash
ssh -p 36000 root@charvelxia-any2.devcloud.woa.com 'cd /opt/feedback_hub && ./.venv/bin/python -m feedback_hub.cli ingest backfill --last 14d --chunk 6h'
```

Expected: every successful six-hour window has a coverage row, duplicates are skipped by `feedback_id`, and no message/conversation label count changes as a side effect. Do not run a 180-day backfill merely because a future topic might request six months; if such coverage is later needed, report the existing gap and obtain separate approval for that larger raw-data pull.

- [ ] **Step 5: Perform checkpointed vector rebuild and start the vector service**

Run:

```bash
ssh -p 36000 root@charvelxia-any2.devcloud.woa.com 'cd /opt/feedback_hub && ./.venv/bin/python -m feedback_hub.cli vectors rebuild --target-model-version qwen3-embedding-0.6b-document-v1 --generation-id qwen3-embedding-0.6b-document-v1-20260721 && scripts/vector_runtime.sh start'
```

Expected: final status reports `pending_count=0`, `dimension=1024`, an active generation, and a vector watermark equal to the latest fully indexed source generation.

- [ ] **Step 6: Run one manual 30-minute incremental cycle**

Run:

```bash
ssh -p 36000 root@charvelxia-any2.devcloud.woa.com 'cd /opt/feedback_hub && ./.venv/bin/python -m feedback_hub.cli pipeline incremental --pull-window 30m'
```

Expected: JSON status is `succeeded`; `inserted_count + skipped_dup_count == fetched_count`; vector status is `succeeded`.

- [ ] **Step 7: Repeat immediately to prove overlap idempotency**

Run the same command again.

Expected: it succeeds, existing `feedback_id` rows are skipped, and no already indexed ID is re-embedded.

- [ ] **Step 8: Install and verify the 20-minute cron**

Run:

```bash
ssh -p 36000 root@charvelxia-any2.devcloud.woa.com 'cd /opt/feedback_hub && scripts/install_feedback_incremental_cron.sh && crontab -l'
```

Expected: exactly one `*/20 * * * *` feedback incremental line; no legacy `0 */2 * * * cd /opt/feedback_hub && PYTHON_BIN=./.venv/bin/python APP_PORT=8000 scripts/feedback_daily_sync.sh --last 150m --no-sync` line.

- [ ] **Step 9: Observe one scheduled cycle and verify service health**

Run:

```bash
ssh -p 36000 root@charvelxia-any2.devcloud.woa.com 'cd /opt/feedback_hub && tail -n 120 feedback_hub/data/logs/feedback_incremental_sync.log && scripts/vector_runtime.sh status && scripts/devcloud_runtime.sh status'
```

Expected: a scheduled run completed, both services are running, vector pending count returns to zero, and the topic capabilities endpoint returns HTTP 200 over localhost.

## Plan Acceptance Gate

- Two overlapping 30-minute pulls do not duplicate raw rows or vectors.
- The pipeline performs no generic tagging.
- Vector failure after pull produces a retryable partial result and leaves source coverage committed.
- The installed cron is exactly every 20 minutes and preserves unrelated jobs.
- Model, database, index, logs, and backups survive regular code deployment.
- Remote mutation did not occur before the explicit approval checkpoint.
