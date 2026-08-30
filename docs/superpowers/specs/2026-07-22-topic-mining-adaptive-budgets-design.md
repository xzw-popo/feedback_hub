# Topic Mining Adaptive Budgets Design

## Goal

Make standard topic-mining runs proportional to their requested time range, bound Agent review work, and export every confirmed match. The backend owns all budgets; the Skill reports them but does not choose retrieval or review parameters.

## Production Evidence

On 2026-07-22 the production source contained approximately:

| Rolling window | Feedback count |
| --- | ---: |
| 1 day | 1,576 |
| 3 days | 4,465 |
| 7 days | 9,141 |
| 14 days | 18,438 |

The representative seven-day voice-input run had this funnel:

```text
9,133 hard-scope rows
  -> 570 retrieved candidates
  -> 500 classified candidates
  -> 308 review-queue items
  -> 321 verified matches
  -> 100 exported rows
```

The 308 review items included 180 negative-query conflicts, 151 vector-only matches, 40 deterministic label samples, 20 high-confidence reject samples, 16 classifier-requested reviews, and 4 low-confidence matches. Reasons overlap. This shows two independent problems: the one-day classification budget is too large, and risk flags are being treated as mandatory review work. The 100-row export cap is a third, unrelated truncation.

## Decisions

### Dynamic standard classification budget

The backend computes:

```text
effective_days = max(1, ceil((end_time - start_time) / 24 hours))
effective_candidate_limit = min(500, max(100, effective_days * 80))
actual_classification_count = min(retrieved_candidate_count, effective_candidate_limit)
```

The resulting limits are:

| Requested duration | Standard candidate limit |
| --- | ---: |
| up to 1 day | 100 |
| over 1 through 2 days | 160 |
| over 2 through 3 days | 240 |
| over 3 through 4 days | 320 |
| over 4 through 5 days | 400 |
| over 5 through 6 days | 480 |
| over 6 days | 500 |

The calculation uses elapsed instants, not calendar labels. A duration of exactly 24 hours is one effective day; 24 hours plus one millisecond is two. Hard-scope filters may reduce the source population, but they do not change the formula; actual classification is naturally bounded by retrieved candidates.

Exhaustive mode retains its existing 5,000-candidate safety maximum. Duration alone never selects exhaustive mode.

### Bounded review queue

Review work has two components.

Mandatory review includes every classification that:

- sets `needs_review=true`; or
- is a matched result with confidence below `0.75`.

Mandatory review is never dropped to satisfy a sampling budget.

Quality-assurance sampling uses:

```text
review_sample_limit = min(80, effective_days * 20)
```

The QA pool has four deterministic strata:

1. matched results retrieved only by vector search;
2. matched results with negative-query conflicts;
3. other matched results;
4. high-confidence not-matched results.

The sample allowance is divided as evenly as possible across non-empty strata. Each stratum is ordered by a stable hash of `run_id`, `item_id`, and stratum name. Duplicate items are emitted once. Empty or exhausted strata surrender their unused allowance, which is redistributed deterministically across remaining strata until the allowance or pool is exhausted. Mandatory items do not consume QA allowance; QA selection excludes items already mandatory.

The queue may exceed 80 only because mandatory review itself can exceed that number. Every queued item still requires an explicit override before verification.

### Export every confirmed match

Standard and exhaustive exports contain every verified matched row:

```text
returned_feedback = matched_total
```

There is no ordinary Excel or JSONL result limit. The workbook remains bounded indirectly by the classification safety limits and is far below Excel's worksheet row limit.

Result scope describes candidate coverage, not export truncation:

```text
possibly_more_matches = retrieved_candidate_count > classified_count
result_scope = "representative" if possibly_more_matches else "reviewed"
```

Thus a standard run can export all 321 confirmed matches while still truthfully reporting that 70 retrieved candidates were not classified. Removing the export cap must never turn a representative run into a claim of completeness.

## Backend Interfaces and Audit Fields

Backend configuration uses:

- `standard_candidate_min = 100`
- `standard_candidates_per_day = 80`
- `standard_candidate_max = 500`
- `review_samples_per_day = 20`
- `review_sample_max = 80`
- `exhaustive_candidate_max = 5000`

Capabilities preserve `run_modes.standard.candidate_limit=500` as the standard safety maximum for older clients, change `run_modes.standard.result_limit` to `null`, and add the dynamic budget policy:

```json
{
  "run_modes": {
    "standard": {
      "candidate_limit": 500,
      "result_limit": null,
      "candidate_budget": {"minimum": 100, "per_day": 80, "maximum": 500},
      "review_sample_budget": {"per_day": 20, "maximum": 80}
    }
  }
}
```

Each new Run records its effective policy and outcome:

```json
{
  "candidate_budget": {
    "effective_days": 7,
    "minimum": 100,
    "per_day": 80,
    "maximum": 500,
    "effective_limit": 500
  },
  "review_budget": {
    "mandatory_count": 20,
    "sample_limit": 80,
    "sampled_count": 76,
    "queue_count": 96
  }
}
```

These fields are backend-produced audit data. The topic spec and Skill expose no new tuning parameter.

## Data Flow

```text
validated topic spec
  -> compute elapsed effective_days and budgets
  -> hard-scope source rows
  -> hybrid retrieval
  -> deterministic diverse selection up to effective_candidate_limit
  -> batch classification
  -> mandatory review plus bounded deterministic QA sample
  -> explicit decisions for every queued item
  -> verification
  -> export every verified matched row
```

Run identity and snapshot behavior remain unchanged. Resume reuses the effective budget persisted in the Run rather than recomputing it from later configuration.

## Compatibility

- New Runs use dynamic classification and bounded review policies.
- A Run whose selected candidate artifact already exists keeps that frozen classification boundary.
- A Run whose review queue already exists keeps that queue; resume does not replace its review obligations.
- Existing verified Runs may be exported again. The new export contains all rows already present in `final_reviewed.jsonl`, so the seven-day voice-input run can produce 321 rows without reclassification.
- Existing clients that read only `candidate_limit` continue to see the 500 safety maximum. Clients must treat `result_limit=null` as no ordinary export cap.

## Failure and Boundary Behavior

- Invalid, missing, one-sided, or non-increasing time boundaries continue to fail during spec preparation or validation.
- Zero retrieval and zero matches are valid and produce empty verified deliverables.
- If fewer candidates are retrieved than the effective limit, all retrieved candidates are classified.
- Mandatory review is complete even when it exceeds the QA sample limit.
- Review reason overlap never duplicates an item.
- Failed Excel generation does not alter verified JSONL or the Run's verified source data.
- Export metadata and workbook metadata must agree exactly with the Run result-scope fields.

## Verification Plan

Automated tests cover:

1. durations of one hour, exactly 24 hours, 24 hours plus one millisecond, two through seven days, 14 days, and six months;
2. the 100 minimum, 500 maximum, and retrieval counts below the effective budget;
3. deterministic review selection, reason overlap deduplication, balanced strata, and unused-allowance redistribution;
4. mandatory review counts greater than the QA allowance;
5. all 321 fixture matches appearing in both Excel and JSONL;
6. `representative/possibly_more_matches=true` when retrieval exceeds classification, despite exporting all confirmed matches;
7. `reviewed/possibly_more_matches=false` when every retrieved candidate is classified;
8. regeneration of an existing verified standard export without the former 100-row cap;
9. one-day and seven-day end-to-end funnels producing effective limits of 100 and 500;
10. capabilities and distributable Skill wording for dynamic budgets and uncapped confirmed exports.

Deployment verification reads capabilities, exercises deterministic fixture Runs, and re-exports a copied verified Run artifact. Production Runs are not mutated merely for smoke testing.

## Out of Scope

- User-configurable candidate or review limits
- A new public Run mode
- Automatic continuation through additional candidate pages
- Changes to embedding, retrieval fusion, classifier prompts, or exhaustive safety limits
- Claims that a standard result is exhaustive when retrieved candidates remain unclassified
