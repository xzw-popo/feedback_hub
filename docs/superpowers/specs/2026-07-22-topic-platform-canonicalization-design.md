# Topic Platform Canonicalization Design

## Problem

Topic scope currently accepts any non-empty `scope.platforms` string and passes it unchanged to exact SQLite and vector-service filters. User-facing names such as `Windows` or `安卓` therefore produce valid-looking runs whose hard scope matches no rows stored under canonical database values such as `Win` and `Android`.

This failure happens before semantic recall and is indistinguishable to the caller from a genuinely empty topic unless the caller already knows the database enum.

## Goals

- Accept common, unambiguous user-facing aliases for the four supported platforms.
- Persist and hash only canonical platform values in every newly created run.
- Apply the same canonical scope to source SQL, vector filters, verification, and export.
- Advertise the platform contract so a distributable Skill does not have to guess backend metadata.
- Reject unsupported or ambiguous values before creating a run instead of silently returning zero candidates.

## Canonical contract

The supported values are `Win`, `Android`, `iOS`, and `Mac`.

Alias matching is case-insensitive and ignores Unicode whitespace. The initial aliases are:

| Canonical value | Accepted aliases |
| --- | --- |
| `Win` | `Win`, `Windows`, `Win端`, `Windows端` |
| `Android` | `Android`, `安卓`, `Android端`, `安卓端` |
| `iOS` | `iOS`, `iOS端` |
| `Mac` | `Mac`, `macOS`, `Mac端`, `macOS端` |

Do not map ambiguous words such as `PC`, `电脑`, `苹果`, `手机`, `iPhone`, or `iPad`. Adding a new canonical value or alias is an explicit backend contract change.

After normalization, duplicate canonical values are removed while preserving first occurrence. An omitted or empty platform list remains empty and means no platform hard filter.

## Backend authority

Add a focused `feedback_hub/topic_mining/platforms.py` module containing the canonical values, alias normalization, and capability serialization. `validate_topic_spec()` calls it while constructing `TopicScope`, so every downstream consumer receives canonical values.

Examples:

```text
["Windows"]              -> ("Win",)
["安卓", "ANDROID"]     -> ("Android",)
["Windows", "Win"]      -> ("Win",)
["Linux"]                -> ValueError("unsupported platform: Linux; supported platforms: Win, Android, iOS, Mac")
```

The API continues mapping invalid run payloads to HTTP 422. Since canonicalization happens before `topic_spec_hash`, aliases and canonical spellings represent the same run identity when all other fields match.

Existing persisted runs are immutable and are not migrated. A run created previously with a noncanonical platform must be recreated.

## Capability discovery

`GET /api/topic-mining/capabilities` adds:

```json
{
  "scope_filters": {
    "platforms": {
      "canonical_values": ["Win", "Android", "iOS", "Mac"],
      "aliases": {
        "Win": "Win",
        "Windows": "Win",
        "Win端": "Win",
        "Windows端": "Win",
        "Android": "Android",
        "安卓": "Android",
        "Android端": "Android",
        "安卓端": "Android",
        "iOS": "iOS",
        "iOS端": "iOS",
        "Mac": "Mac",
        "macOS": "Mac",
        "Mac端": "Mac",
        "macOS端": "Mac"
      },
      "matching": "case_insensitive_ignore_whitespace"
    }
  }
}
```

The payload is generated from the same backend constants used by validation, preventing backend documentation drift.

## Skill preparation

`topic_backend_client.py prepare-spec` reads the platform contract from capabilities and passes it to the bundled validator. Before JSON Schema validation, the validator canonicalizes `scope.platforms`; the prepared JSON written to disk therefore contains only canonical values.

The bundled schema restricts prepared `scope.platforms` items to the four canonical values. Direct `create-run` calls remain safe because backend validation independently canonicalizes aliases and rejects unknown values.

If a backend omits or malforms the advertised platform contract, `prepare-spec` fails with a clear local configuration error instead of using a stale hard-coded alias table. The Skill documentation tells the Agent to preserve an explicitly requested platform but rely on `prepare-spec` for canonical spelling.

## Error behavior

- Unsupported and ambiguous platform values fail before snapshot construction or worker scheduling.
- Errors include the offending input and the supported canonical values, but do not expose database contents or configuration secrets.
- Multiple invalid values report the first invalid value deterministically.
- Existing time, product, channel, and version behavior is unchanged.

## Verification

- Backend contract tests cover canonical values, case variants, whitespace, Chinese aliases, suffix aliases, deduplication, and unsupported values.
- API tests prove capabilities and HTTP 422 behavior and confirm invalid payloads schedule no worker.
- Source/vector tests prove `Windows` reaches `Win` rows and `安卓` produces an `Android` vector filter.
- Skill client tests prove `prepare-spec` writes canonical JSON from the advertised contract and fails closed on missing, malformed, or unknown platform metadata.
- Skill schema/documentation tests require canonical values and explain the alias behavior.
- An end-to-end topic run with `scope.platforms=["Windows"]` returns only database rows whose platform is `Win`.
- Existing topic-mining and Skill-package suites remain green.
