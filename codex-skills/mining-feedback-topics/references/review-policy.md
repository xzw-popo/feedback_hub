# Caller-AI Decision Policy

The calling AI owns every semantic decision. The backend supplies frozen recall candidates, source text, `context_items`, and the user's complete inclusion/exclusion boundary; similarity scores are retrieval hints, never labels.

For every candidate page, return exactly one JSON decision per item:

```json
{"item_id":"...","label":"matched","reason":"...","evidence":["exact source substring"]}
```

Allowed fields are only `item_id`, `label`, `reason`, and `evidence`. Use `matched` only when the inclusion criteria are affirmatively satisfied and no exclusion applies. Every matched decision needs one or more exact, non-empty substrings from `item.text` or a supplied context text. Use `not_matched` when evidence is insufficient or an exclusion applies; its evidence must be `[]`. Explain the boundary in `reason`.

Fetch at most 20 candidates per page. Do not omit, add, or duplicate page IDs. Run the bundled local validator through `apply-classifications`; it must pass before the request is made. The backend validates each item again and may accept valid siblings while rejecting individual invalid decisions. A partial HTTP 200 is progress, not Run failure: keep accepted items, repair only rejected/pending IDs, and never reclassify accepted siblings merely because one item failed.

Continue until every frozen candidate is accepted and the Run reaches `verification_ready`. Verification rechecks exact coverage and evidence against authenticated source artifacts. Decisions affect only this Run's audited result; they never change source feedback or formal labels.
