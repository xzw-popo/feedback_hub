# Topic Source Watermark Design

## Goal

Anchor default and rolling topic windows to the backend's latest continuously covered source time instead of an Agent's wall clock, eliminating the uncovered tail created by the 20-minute ingestion cadence.

## Backend contract

`GET /api/topic-mining/capabilities` adds `source_freshness`:

- `ready`: whether a common continuous coverage interval exists.
- `available_from` / `available_from_ms`: start of the latest common continuous interval.
- `available_through` / `available_through_ms`: exclusive end of that interval and the safe topic-window anchor.
- `source_generation_ms`: latest successful coverage completion generation; it remains distinct from `available_through_ms`.
- `observed_at_ms`: backend observation time.
- `freshness_lag_seconds`: non-negative difference between observation time and `available_through_ms`.
- `sync_interval_seconds`: configured ingestion cadence, currently 1200 seconds.

The backend computes the latest merged continuous interval for every recorded source channel and returns their common intersection. Missing coverage returns `ready: false` with nullable time fields; capabilities remains inspectable.

## Client and Skill workflow

Add a deterministic client command:

```bash
python3 scripts/topic_backend_client.py prepare-spec \
  --spec TOPIC_SPEC_PATH \
  --output PREPARED_SPEC_PATH \
  [--window-days DAYS]
```

The command fetches capabilities, requires ready source freshness, and invokes the bundled validator with `available_through` as `--default-now`. If both time boundaries are absent, it uses `--window-days`; otherwise it uses the backend `default_time_days` value, currently 14. A complete explicit time pair passes unchanged. A one-sided pair remains invalid.

For a user request such as “最近一周”, the Agent omits both exact boundaries from the draft spec and calls `prepare-spec --window-days 7`. It must not calculate a wall-clock endpoint. Calendar-relative ranges that cannot be represented as whole days are calculated from the advertised `available_through`, never local time.

## Failure behavior

- Missing or malformed freshness metadata is a local/client preparation error; no run is created.
- Explicit historical boundaries remain hard boundaries and still receive backend continuous-coverage validation.
- The backend never silently clamps a complete explicit time pair.
- A sync in progress continues advertising the last committed stable interval until the new interval is committed.

## Verification

- Source tests cover merged intervals, multi-channel common coverage, and unavailable coverage.
- API tests cover the exact freshness response and the configured 1200-second cadence.
- Client tests prove `prepare-spec` uses `available_through`, preserves source files, handles explicit pairs, and rejects unavailable freshness.
- Skill contract tests forbid wall-clock anchoring and require the waterline-aware command for default and relative windows.
- A forward test with “最近一周” must produce an end time equal to the backend-advertised `available_through`.
