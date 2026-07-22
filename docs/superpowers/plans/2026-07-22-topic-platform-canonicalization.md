# Topic Platform Canonicalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Canonicalize common platform aliases before topic hard-scope filtering, advertise the mapping through capabilities, and make the distributable Skill prepare canonical specs.

**Architecture:** A focused backend platform-contract module is the authority for canonical values, aliases, normalization, and capability serialization. New-run validation canonicalizes aliases before hashing; persisted-run loading preserves historical strings. The Skill client consumes the advertised contract and passes it to its offline validator before schema validation.

**Tech Stack:** Python 3.11, FastAPI, SQLite, JSON Schema, pytest

## Global Constraints

- Canonical platform values are exactly `Win`, `Android`, `iOS`, and `Mac` in that order.
- Alias matching is case-insensitive and ignores Unicode whitespace.
- Do not map ambiguous values including `PC`, `电脑`, `苹果`, `手机`, `iPhone`, or `iPad`.
- Empty platform scope remains empty and means no platform hard filter.
- Unknown platforms fail before snapshot construction or worker scheduling.
- New runs persist and hash canonical values; persisted legacy runs retain their original strings and identity.
- The Skill must consume the backend-advertised contract and fail closed if it is absent or malformed.
- Do not modify or stage the user-owned untracked root `WORKSPACE_GUIDE.md`.

---

### Task 1: Backend platform contract and new-run normalization

**Files:**
- Create: `feedback_hub/topic_mining/platforms.py`
- Modify: `feedback_hub/topic_mining/contracts.py:152-180,253-300`
- Modify: `feedback_hub/tests/test_topic_mining_contracts.py`

**Interfaces:**
- Produces: `CANONICAL_PLATFORMS: tuple[str, ...]`, `normalize_platforms(values: Sequence[str]) -> tuple[str, ...]`, and `platform_capability() -> dict[str, Any]`.
- Produces: `validate_topic_spec()` with canonical platform scope and `load_persisted_topic_spec()` with historical scope preserved.

- [ ] **Step 1: Write failing platform-contract tests**

Add tests covering canonicalization, deduplication, errors, hashes, and persisted compatibility:

```python
@pytest.mark.parametrize(
    ("raw_values", "expected"),
    [
        (["Win", "Android", "iOS", "Mac"], ("Win", "Android", "iOS", "Mac")),
        ([" windows ", "WIN端"], ("Win",)),
        (["安卓", "A N D R O I D端"], ("Android",)),
        (["ios端"], ("iOS",)),
        (["macOS", "M A C端"], ("Mac",)),
    ],
)
def test_new_topic_spec_canonicalizes_platform_aliases(raw_values, expected):
    raw = valid_spec()
    raw["scope"]["platforms"] = raw_values
    assert validate_topic_spec(raw).scope.platforms == expected


@pytest.mark.parametrize("unsupported", ["Linux", "PC", "电脑", "苹果", "iPhone", "iPad"])
def test_new_topic_spec_rejects_unsupported_or_ambiguous_platform(unsupported):
    raw = valid_spec()
    raw["scope"]["platforms"] = [unsupported]
    with pytest.raises(
        ValueError,
        match=rf"unsupported platform: {unsupported}; supported platforms: Win, Android, iOS, Mac",
    ):
        validate_topic_spec(raw)


def test_alias_and_canonical_platform_have_same_new_run_hash():
    alias = valid_spec()
    alias["scope"]["platforms"] = ["Windows"]
    canonical = valid_spec()
    assert topic_spec_hash(validate_topic_spec(alias)) == topic_spec_hash(validate_topic_spec(canonical))


def test_persisted_topic_spec_preserves_legacy_noncanonical_platform_identity():
    raw = valid_spec()
    raw["scope"]["platforms"] = ["Windows"]
    persisted = contracts.load_persisted_topic_spec(raw)
    assert persisted.scope.platforms == ("Windows",)
    assert persisted.to_dict()["scope"]["platforms"] == ["Windows"]
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining_contracts.py \
  -k 'platform_aliases or unsupported_or_ambiguous_platform or same_new_run_hash or preserves_legacy_noncanonical' -q
```

Expected: alias tests retain raw values, unsupported values are accepted, and hash equality fails.

- [ ] **Step 3: Implement the backend platform authority**

Create `platforms.py` with the exact contract:

```python
from __future__ import annotations

from typing import Any, Sequence

CANONICAL_PLATFORMS = ("Win", "Android", "iOS", "Mac")
PLATFORM_ALIASES = {
    "Win": "Win", "Windows": "Win", "Win端": "Win", "Windows端": "Win",
    "Android": "Android", "安卓": "Android", "Android端": "Android", "安卓端": "Android",
    "iOS": "iOS", "iOS端": "iOS",
    "Mac": "Mac", "macOS": "Mac", "Mac端": "Mac", "macOS端": "Mac",
}


def _alias_key(value: str) -> str:
    return "".join(value.split()).casefold()


_CANONICAL_BY_ALIAS = {
    _alias_key(alias): canonical for alias, canonical in PLATFORM_ALIASES.items()
}


def normalize_platforms(values: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        canonical = _CANONICAL_BY_ALIAS.get(_alias_key(value))
        if canonical is None:
            supported = ", ".join(CANONICAL_PLATFORMS)
            raise ValueError(
                f"unsupported platform: {value}; supported platforms: {supported}"
            )
        if canonical not in seen:
            seen.add(canonical)
            normalized.append(canonical)
    return tuple(normalized)


def platform_capability() -> dict[str, Any]:
    return {
        "canonical_values": list(CANONICAL_PLATFORMS),
        "aliases": dict(PLATFORM_ALIASES),
        "matching": "case_insensitive_ignore_whitespace",
    }
```

Extend `_validate_topic_spec` with `canonicalize_platforms: bool`. Parse the string list first, then call `normalize_platforms` only for `validate_topic_spec`; pass `False` from `load_persisted_topic_spec`.

Change the backend schema's `scope.platforms.items` to:

```python
{"enum": list(CANONICAL_PLATFORMS)}
```

- [ ] **Step 4: Run contract tests and verify GREEN**

Run: `python3 -m pytest feedback_hub/tests/test_topic_mining_contracts.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add feedback_hub/topic_mining/platforms.py feedback_hub/topic_mining/contracts.py \
  feedback_hub/tests/test_topic_mining_contracts.py
git commit -m "feat: canonicalize topic platform scope"
```

---

### Task 2: Capabilities and API fail-fast behavior

**Files:**
- Modify: `feedback_hub/topic_mining/api.py:192-245`
- Modify: `feedback_hub/tests/test_topic_mining_api.py:70-105`
- Modify: `feedback_hub/tests/test_topic_mining_source.py`
- Modify: `feedback_hub/tests/test_topic_mining_end_to_end.py`

**Interfaces:**
- Consumes: `platform_capability()` and canonicalized `validate_topic_spec()` from Task 1.
- Produces: `capabilities.scope_filters.platforms` and HTTP 422 for unsupported platforms before scheduling.

- [ ] **Step 1: Write failing API and data-path tests**

Extend the capabilities assertion:

```python
assert payload["scope_filters"]["platforms"] == {
    "canonical_values": ["Win", "Android", "iOS", "Mac"],
    "aliases": {
        "Win": "Win", "Windows": "Win", "Win端": "Win", "Windows端": "Win",
        "Android": "Android", "安卓": "Android", "Android端": "Android", "安卓端": "Android",
        "iOS": "iOS", "iOS端": "iOS",
        "Mac": "Mac", "macOS": "Mac", "Mac端": "Mac", "macOS端": "Mac",
    },
    "matching": "case_insensitive_ignore_whitespace",
}
```

Add a create-run test that monkeypatches `start_run_async`, submits `platforms: ["Linux"]`, and asserts HTTP 422, an `unsupported platform` detail, and no scheduled run.

Add a source test that passes `validate_topic_spec()` a `Windows` scope and asserts `_filtered_rows`/snapshot contains only database `Win` rows. Update the end-to-end fixture's spec from `Win` to `Windows` and retain its assertions that only Win rows reach recall/export and the vector request receives `filters["platforms"] == ["Win"]`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_api.py -k 'capabilities_advertise or unsupported_platform' \
  feedback_hub/tests/test_topic_mining_source.py -k windows_alias \
  feedback_hub/tests/test_topic_mining_end_to_end.py -q
```

Expected: capabilities lacks `scope_filters`; unsupported platform schedules/creates a run or is not rejected. The data-path tests may become green after Task 1, which is acceptable because they verify downstream use of the Task 1 interface.

- [ ] **Step 3: Advertise the backend contract**

Import `platform_capability` into `api.py` and add this field to the capabilities response:

```python
"scope_filters": {"platforms": platform_capability()},
```

Do not add special SQL or vector alias logic; both must receive the already-canonical `TopicSpec`.

- [ ] **Step 4: Run API and data-path tests and verify GREEN**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_topic_mining_api.py \
  feedback_hub/tests/test_topic_mining_source.py \
  feedback_hub/tests/test_topic_mining_end_to_end.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add feedback_hub/topic_mining/api.py feedback_hub/tests/test_topic_mining_api.py \
  feedback_hub/tests/test_topic_mining_source.py feedback_hub/tests/test_topic_mining_end_to_end.py
git commit -m "feat: advertise topic platform contract"
```

---

### Task 3: Skill platform preparation and documentation

**Files:**
- Modify: `codex-skills/mining-feedback-topics/scripts/topic_backend_client.py:184-220`
- Modify: `codex-skills/mining-feedback-topics/scripts/validate_topic_spec.py`
- Modify: `codex-skills/mining-feedback-topics/references/topic-spec.schema.json`
- Modify: `codex-skills/mining-feedback-topics/references/topic-spec.md`
- Modify: `codex-skills/mining-feedback-topics/references/backend-contract.md`
- Modify: `codex-skills/mining-feedback-topics/SKILL.md`
- Modify: `feedback_hub/tests/test_mining_feedback_topics_skill_package.py`

**Interfaces:**
- Consumes: `capabilities.scope_filters.platforms` from Task 2.
- Produces: validator option `--platform-contract-json JSON` and prepared specs containing only canonical platforms.

- [ ] **Step 1: Write failing Skill preparation tests**

Add tests using the existing fake HTTP/client harness:

```python
def test_prepare_spec_canonicalizes_platform_from_capabilities(tmp_path, monkeypatch):
    module = _load_client_module()
    source = tmp_path / "topic.json"
    prepared = tmp_path / "prepared.json"
    raw = valid_spec()
    raw["scope"].pop("start_time")
    raw["scope"].pop("end_time")
    raw["scope"]["platforms"] = ["Windows", "WIN"]
    source.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    capabilities = {
        "default_time_days": 14,
        "source_freshness": {
            "ready": True,
            "available_through": "2026-07-22T10:00:00+08:00",
        },
        "scope_filters": {
            "platforms": {
                "canonical_values": ["Win", "Android", "iOS", "Mac"],
                "aliases": {
                    "Win": "Win", "Windows": "Win", "Win端": "Win", "Windows端": "Win",
                    "Android": "Android", "安卓": "Android", "Android端": "Android", "安卓端": "Android",
                    "iOS": "iOS", "iOS端": "iOS",
                    "Mac": "Mac", "macOS": "Mac", "Mac端": "Mac", "macOS端": "Mac",
                },
                "matching": "case_insensitive_ignore_whitespace",
            }
        },
    }
    monkeypatch.setattr(
        module, "_request",
        lambda *_args, **_kwargs: (capabilities, b"{}"),
    )

    code, stdout, stderr = _run_client(
        module,
        ["--base-url", "https://topic.internal", "prepare-spec",
         "--spec", str(source), "--output", str(prepared)],
    )

    assert code == 0, stderr
    assert json.loads(prepared.read_text(encoding="utf-8"))["scope"]["platforms"] == ["Win"]
    assert json.loads(stdout)["scope"]["platforms"] == ["Win"]
```

Add parameterized failures for missing `scope_filters`, malformed `canonical_values`, aliases targeting an unknown canonical value, unsupported platform `Linux`, and ambiguous `PC`. Assert exit code 2, a concise platform-contract/platform error, no traceback, and no prepared output replacement.

Add a direct-validator test proving an alias is rejected without `--platform-contract-json`, and accepted/canonicalized with the advertised contract.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py \
  -k 'platform and (prepare or validator or capabilities)' -q
```

Expected: prepare-spec preserves `Windows`, and malformed capability cases do not fail closed.

- [ ] **Step 3: Implement validator contract parsing and normalization**

Add these validator responsibilities before `_validate(...)`:

```python
def _platform_key(value: str) -> str:
    return "".join(value.split()).casefold()


def _parse_platform_contract(raw: str) -> tuple[tuple[str, ...], dict[str, str]]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise _error("--platform-contract-json", "must be valid JSON") from error
    if not isinstance(value, dict):
        raise _error("--platform-contract-json", "must be an object")
    canonical_raw = value.get("canonical_values")
    expected = ("Win", "Android", "iOS", "Mac")
    if (
        not isinstance(canonical_raw, list)
        or tuple(canonical_raw) != expected
        or any(not isinstance(item, str) for item in canonical_raw)
    ):
        raise _error(
            "--platform-contract-json.canonical_values",
            "must equal Win, Android, iOS, Mac in canonical order",
        )
    if value.get("matching") != "case_insensitive_ignore_whitespace":
        raise _error(
            "--platform-contract-json.matching",
            "must equal case_insensitive_ignore_whitespace",
        )
    aliases_raw = value.get("aliases")
    if not isinstance(aliases_raw, dict) or not aliases_raw:
        raise _error("--platform-contract-json.aliases", "must be a non-empty object")
    aliases: dict[str, str] = {}
    for alias, target in aliases_raw.items():
        if not isinstance(alias, str) or not alias.strip() or not isinstance(target, str):
            raise _error(
                "--platform-contract-json.aliases",
                "must map non-empty strings to canonical platform strings",
            )
        if target not in expected:
            raise _error(
                f"--platform-contract-json.aliases.{alias}",
                "must target a canonical platform",
            )
        key = _platform_key(alias)
        if key in aliases and aliases[key] != target:
            raise _error(
                "--platform-contract-json.aliases",
                "contains conflicting normalized aliases",
            )
        aliases[key] = target
    for canonical in expected:
        if aliases.get(_platform_key(canonical)) != canonical:
            raise _error(
                "--platform-contract-json.aliases",
                f"must contain the canonical self-alias: {canonical}",
            )
    return expected, aliases


def _prepare_platform_scope(value: Any, raw_contract: str | None) -> None:
    if raw_contract is None or not isinstance(value, dict):
        return
    canonical_values, aliases = _parse_platform_contract(raw_contract)
    scope = value.get("scope")
    if not isinstance(scope, dict):
        return
    platforms = scope.get("platforms", [])
    if not isinstance(platforms, list) or any(not isinstance(item, str) for item in platforms):
        return
    normalized: list[str] = []
    seen: set[str] = set()
    for index, platform in enumerate(platforms):
        canonical = aliases.get(_platform_key(platform))
        if canonical is None:
            supported = ", ".join(canonical_values)
            raise _error(
                f"$.scope.platforms[{index}]",
                f"unsupported platform: {platform}; supported platforms: {supported}",
            )
        if canonical not in seen:
            seen.add(canonical)
            normalized.append(canonical)
    scope["platforms"] = normalized
```

Add `--platform-contract-json` to the validator CLI. Call `_prepare_platform_scope` after default-time preparation and before schema validation.

In `topic_backend_client.py`, strictly extract `capabilities["scope_filters"]["platforms"]`; reject missing/malformed structures locally. Pass compact JSON through `--platform-contract-json` in the validator subprocess argument list.

- [ ] **Step 4: Update bundled schema and Skill guidance**

Regenerate or edit `topic-spec.schema.json` so `scope.platforms.items.enum` is `["Win", "Android", "iOS", "Mac"]` and it remains byte-structure equivalent to `topic_spec_json_schema()`.

Document that:

- the Agent preserves explicit platform intent and does not invent platform scope;
- `prepare-spec` canonicalizes aliases using capabilities;
- only prepared JSON is sent to `create-run`;
- unsupported or ambiguous platforms block run creation;
- examples include `Windows -> Win` and `安卓 -> Android`.

- [ ] **Step 5: Run Skill tests and verify GREEN**

Run:

```bash
python3 -m pytest feedback_hub/tests/test_mining_feedback_topics_skill_package.py -q
```

Expected: the complete Skill package suite passes.

- [ ] **Step 6: Commit**

```bash
git add codex-skills/mining-feedback-topics feedback_hub/tests/test_mining_feedback_topics_skill_package.py
git commit -m "feat: prepare canonical topic platforms"
```

---

### Task 4: Full regression and contract audit

**Files:**
- Modify only if a failing in-scope regression first receives a focused failing test.

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces: verified canonical platform behavior across backend and Skill.

- [ ] **Step 1: Audit platform entry points**

Run:

```bash
rg -n "scope\.platforms|\[\"platforms\"\]|platform_capability|normalize_platforms" \
  feedback_hub/topic_mining codex-skills/mining-feedback-topics
```

Expected: every new-run entry point either normalizes through backend validation or consumes the advertised Skill contract; SQL/vector code only receives canonical values.

- [ ] **Step 2: Run the complete focused suite**

Run:

```bash
python3 -m pytest \
  feedback_hub/tests/test_topic_mining_contracts.py \
  feedback_hub/tests/test_topic_mining_api.py \
  feedback_hub/tests/test_topic_mining_source.py \
  feedback_hub/tests/test_topic_mining_retrieval.py \
  feedback_hub/tests/test_topic_mining_vector_client.py \
  feedback_hub/tests/test_topic_mining_service.py \
  feedback_hub/tests/test_topic_mining_end_to_end.py \
  feedback_hub/tests/test_mining_feedback_topics_skill_package.py -q
```

Expected: all tests pass with no new warnings.

- [ ] **Step 3: Run static consistency checks**

Run:

```bash
python3 -m compileall -q feedback_hub/topic_mining codex-skills/mining-feedback-topics/scripts
git diff --check
git status --short
```

Expected: compilation and diff checks succeed; status contains no unintended files and the user-owned `WORKSPACE_GUIDE.md` remains untracked.

- [ ] **Step 4: Commit any regression correction**

If Steps 1-3 expose an in-scope omission, add a focused failing test, verify RED, implement the smallest fix, verify GREEN plus the full focused suite, and commit only those files. If no correction is needed, do not create an empty commit.
