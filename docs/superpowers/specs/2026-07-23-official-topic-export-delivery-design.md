# Official Topic Export Delivery Design

## Goal

Ensure every `mining-feedback-topics` run presents the backend-exported Excel workbook as the primary deliverable. An Agent-generated or transformed workbook may be offered only as an additional artifact and must never replace, hide, or impersonate the official export.

## Scope

This change updates the distributed Skill contract and its static tests. It does not change the backend API, export schema, CLI protocol, or workbook contents.

## Delivery Contract

After `export` and `download` succeed, the Agent must:

1. Preserve the downloaded backend artifact without modifying it.
2. Present that exact local file as a clickable link in the final response, labeled as the backend official export.
3. Treat any reformatted, filtered, summarized, annotated, or otherwise Agent-generated workbook as an optional supplementary artifact.
4. Label supplementary files clearly and present them after the official export.
5. Never rename a supplementary file so that it appears to be the backend official export.

If the official export or download fails, the Agent must report that failure explicitly. It must not substitute a self-generated workbook while claiming the official deliverable was produced.

## Skill Changes

Update `codex-skills/mining-feedback-topics/SKILL.md` in three places:

- Strengthen the Export/Deliver workflow so the official downloaded file is mandatory and primary.
- Add a concise final-response shape that reserves the first artifact slot for the official Excel file.
- Add a common-mistake rule covering replacement by Agent-processed workbooks.

Keep detailed backend behavior in `references/backend-contract.md`; add only the artifact identity and delivery requirements needed to remove ambiguity.

## Validation

Add static tests that fail unless the Skill states all of these invariants:

- the backend-exported Excel must be presented;
- it must be linked as the primary official artifact;
- Agent-generated workbooks are supplementary only;
- a failed official export cannot be hidden by a substitute workbook.

Run the targeted Skill tests, the Skill validator, and repository diff checks before committing and pushing to the existing pull request.

