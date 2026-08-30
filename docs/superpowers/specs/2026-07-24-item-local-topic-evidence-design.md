# Item-Local Topic Evidence Design

## Goal

Ensure every feedback row exported by `mining-feedback-topics` is independently relevant to the requested topic. Conversation context may help interpret a candidate, but it must never be the sole basis for including that candidate in a feedback-level result.

## Membership Semantics

The unit remains `feedback`.

- A candidate may be labeled `matched` only when its own `item.text` affirmatively satisfies the topic boundary.
- Every `matched` evidence fragment must be an exact, non-empty substring of that candidate's `item.text`.
- `context_items` remain visible to the calling AI for interpreting pronouns, terminology, sequence, and exclusions.
- A context item cannot supply membership evidence for the candidate.
- A candidate whose own text is insufficient must be `not_matched`, even when a context item is clearly relevant.
- A relevant context feedback may appear in the result only if retrieval selects it as its own candidate under its own Feedback ID and it independently passes scope and classification.

This intentionally favors precision over recovering vague replies such as “我也是” or “还是不行”.

## Protocol Versioning

Introduce caller-AI classification protocol v3:

- `owner`: `caller_ai`
- `matched_evidence`: `exact_candidate_substring`
- candidate paging and partial-acceptance behavior remain unchanged

Protocol version remains part of Run identity, so the same spec and source watermark produce a distinct v3 Run instead of reusing v2.

Compatibility rules:

- Verified v1/v2 Runs remain readable and exportable as immutable historical artifacts.
- New Runs are always v3.
- Nonterminal v2 Runs do not accept new decisions or verification under v3 semantics; callers must create a new v3 Run.
- v3 decision persistence, paging, status reporting, verification, and export use the existing caller-AI workflow.

## Validation Layers

All validation layers must enforce the same item-local rule:

1. The bundled local decision validator checks `evidence` only against `item.text`.
2. The backend submission validator checks `evidence` only against the candidate text.
3. Verify revalidates persisted v3 decisions and final rows against candidate text only.
4. The protocol capability and Skill documentation describe candidate-only evidence.

`context_items` are still transported but are excluded from authoritative membership evidence sources for v3.

When evidence exists only in context, submission returns the stable per-item code `evidence_not_candidate_grounded`. Accepted sibling decisions remain accepted, matching the current partial-acceptance contract.

## Data Flow

1. Retrieval selects feedback candidates and separately attaches same-conversation context.
2. The Skill fetches a candidate page and reads both the candidate and context.
3. The calling AI decides whether the candidate itself is relevant.
4. A matched decision quotes only candidate text.
5. Local and backend validators reject context-only evidence.
6. Verify materializes the matched candidate as `source_item`.
7. Export writes that same candidate to JSONL and Excel.

The classified identity, supporting evidence, exported source row, Feedback ID, and link therefore all refer to the same feedback item.

## Skill Changes

Update the distributed Skill and references to require exact caller-AI v3 ownership and candidate-only evidence. Explicitly say:

- context is interpretive only;
- never mark a candidate matched solely because a neighboring message is relevant;
- vague candidates that cannot self-prove membership are `not_matched`;
- do not promote or substitute a context item under the candidate's ID.

## Testing

Add regression coverage for:

- candidate unrelated + context related + context-only evidence: rejected;
- candidate related + matching candidate evidence + context present: accepted;
- mixed page: valid siblings accepted while the context-only decision is rejected;
- v3 Verify rejects forged persisted context-only evidence;
- final-row verification rejects context-only evidence;
- v3 Run identity differs from v2 at the same spec and watermark;
- verified v2 artifacts remain readable/exportable;
- Skill package and capabilities advertise only `exact_candidate_substring`;
- end-to-end v3 output contains no feedback whose evidence source is context.

Run the caller-classification, service, API, end-to-end, export, and Skill package suites before deployment.

## Deployment

Deploy backend and Skill together because clients require an exact protocol version. After deployment:

- confirm capabilities advertise v3 and `exact_candidate_substring`;
- create a new smoke Run;
- submit one deliberately context-only match and confirm item-level rejection;
- complete the smoke Run with a corrected `not_matched` decision;
- verify and export the resulting workbook.

Existing topic workbooks are not rewritten. Affected topic requests must be rerun to obtain v3 results.
