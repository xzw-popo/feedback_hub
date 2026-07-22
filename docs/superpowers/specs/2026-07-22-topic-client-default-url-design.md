# Topic Client Default URL Design

## Goal

Make the distributable `mining-feedback-topics` Skill work on the company network or VPN without requiring callers to configure `FEEDBACK_TOPIC_API_URL` first.

## Design

The bundled client owns the deployment default because `SKILL.md` cannot set a child process environment. Resolve the API origin in this order:

1. explicit `--base-url`;
2. non-empty `FEEDBACK_TOPIC_API_URL`;
3. `http://charvelxia-any2.devcloud.woa.com:8000`.

The client continues to append `/api/topic-mining`. The packaged default therefore contains only the origin and port. Explicit overrides retain the current absolute HTTP(S) URL validation.

Update `SKILL.md` and `references/backend-contract.md` to describe the packaged internal default, optional override, VPN/internal-network requirement, and unused token. Do not place credentials in the Skill.

## Tests

- Prove a client invocation without either override reaches the packaged default.
- Preserve environment-variable and CLI precedence.
- Reject an explicitly supplied blank or malformed override rather than silently falling back.
- Assert Skill and backend-contract text no longer require configuration.
- Run the Skill package suite and `quick_validate.py`.
