# Topic Excel Delivery and Hyperlink Integrity Design

## Goal

Ensure every `mining-feedback-topics` run presents the backend-exported Excel workbook as the primary deliverable. An Agent-generated or transformed workbook may be offered only as an additional artifact and must never replace, hide, or impersonate the official export.

Ensure every Excel workbook presented by the Agent preserves clickable feedback links regardless of additional user-requested filtering, sorting, grouping, labeling, annotation, sheet restructuring, or other processing.

## Scope

This change updates the distributed Skill contract, adds a deterministic workbook link-integrity tool, and extends package tests. It does not change the backend API, export schema, or CLI protocol.

## Delivery Contract

After `export` and `download` succeed, the Agent must:

1. Preserve the downloaded backend artifact without modifying it.
2. Present that exact local file as a clickable link in the final response, labeled as the backend official export.
3. Treat any reformatted, filtered, summarized, annotated, or otherwise Agent-generated workbook as an optional supplementary artifact.
4. Label supplementary files clearly and present them after the official export.
5. Never rename a supplementary file so that it appears to be the backend official export.
6. Run every final `.xlsx` artifact through the hyperlink-integrity gate before presenting it.

If the official export or download fails, the Agent must report that failure explicitly. It must not substitute a self-generated workbook while claiming the official deliverable was produced.

## Universal Hyperlink-Integrity Gate

The backend-exported `feedback_list.xlsx` is the source of truth for feedback IDs and link targets. Any derived workbook may add or remove feedback rows, reorder them, add arbitrary columns, create additional sheets, or change presentation, but it must preserve `Feedback ID` for every row that represents an individual feedback item.

The bundled integrity tool accepts the official workbook and one final workbook. It:

1. Scans every worksheet containing individual feedback rows.
2. Locates the `Feedback ID` and feedback-link columns by supported headers.
3. Resolves each link target from the official workbook by `Feedback ID`.
4. Rewrites each feedback link as an Excel `HYPERLINK` formula, an external hyperlink relationship, and Hyperlink style.
5. Preserves unrelated workbook content, sheets, ordering, formatting, and Agent-added columns.
6. Saves a distinct repaired output and validates that every feedback row has a known ID and clickable link.

The tool fails closed when a feedback-bearing sheet has missing identity/link columns, a duplicated or unknown feedback ID, a missing link target, or a link that remains plain text. A failed workbook must not be presented.

A summary-only worksheet need not contain feedback links. If the final workbook contains only summaries, the Agent must either retain a separately validated item-level worksheet or present the official backend workbook alongside it. The official workbook remains mandatory in all cases.

## Skill Changes

Update `codex-skills/mining-feedback-topics/SKILL.md` to:

- Strengthen the Export/Deliver workflow so the official downloaded file is mandatory and primary.
- Require the integrity gate for every Excel artifact, regardless of why it was transformed.
- Define a concise final-response shape that reserves the first artifact slot for the official Excel file.
- State that an unvalidated or plain-text-link workbook is not deliverable.

Add the deterministic tool under the Skill's `scripts/` directory. Keep detailed backend behavior in `references/backend-contract.md`; add only the artifact identity and delivery requirements needed to remove ambiguity.

## Validation

Add tool and static tests that fail unless all of these invariants hold:

- the backend-exported Excel must be presented;
- it must be linked as the primary official artifact;
- Agent-generated workbooks are supplementary only;
- a failed official export cannot be hidden by a substitute workbook.
- every final Excel artifact must pass the hyperlink-integrity gate;
- filtered, reordered, relabeled, and multi-sheet workbooks retain clickable links;
- plain-text links, unknown IDs, missing identity/link columns, and missing targets fail closed;
- unrelated workbook content and Agent-added columns survive repair.

Run the targeted Skill tests, the Skill validator, and repository diff checks before committing and pushing to the existing pull request.
