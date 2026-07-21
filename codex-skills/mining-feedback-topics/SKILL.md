---
name: mining-feedback-topics
description: Use when a user needs to mine, collect, or audit a one-off feedback issue or topic from internal feedback data, especially when strict inclusion/exclusion boundaries, semantic recall, source evidence, links, or a downloadable result are required.
---

# Mining Feedback Topics

Let the backend do data work; use AI judgment for topic boundaries.

Use only the bundled client. Do not query source databases or vector tools directly. Require `FEEDBACK_TOPIC_API_URL`; `FEEDBACK_TOPIC_API_TOKEN` is unused for the current internal deployment. For plan-only requests, do not claim artifacts, results, or validation. Do not expand the user's named object into a different object or product. Keep each user-named target object and behavior as required inclusion conditions; use only meaning-preserving paraphrases. Put unrequested adjacent objects or behaviors only in exclusion criteria.

A blocked pre-run response contains four slots: proposed inclusion, proposed exclusion, one material scope question when needed, and the service or configuration blocker. Treat an explicit relative time range as supplied scope; resolve it from the current date and timezone without asking again. With neither boundary, default to the most recent 14 days from capabilities `default_time_days=14`; never default to all history. When exactly one boundary is supplied or timezone is unknowable, ask: “What start time, end time, and timezone should this run use?”

1. **Capabilities.** Run [topic_backend_client.py](scripts/topic_backend_client.py) with `capabilities`. Confirm schema versions, formats, statuses, read-only flags, default time, and run limits before promising coverage.
2. **Validate.** Read the [topic spec](references/topic-spec.md). Populate each hard-scope field only with values explicit in the current request; leave every other hard-scope field empty except the time default. Set `mode: standard` unless the user explicitly requests all, complete, or exhaustive results; only then set `mode: exhaustive`. Six months alone does not mean completeness. Run [validate_topic_spec.py](scripts/validate_topic_spec.py) with `TOPIC_SPEC_PATH`; repair errors.
3. **Create.** Run `create-run --spec FILE`. Reuse its ID; never replace a running run.
4. **Inspect.** Read the [backend contract](references/backend-contract.md); copy client commands from the backend contract verbatim. Poll `get-run RUN_ID`; inspect funnel counts, data cutoff, vector watermark, and unresolved failures. For `paused_quota_exhausted` or another repairable `failed` run, repair the reported service/configuration blocker and `resume RUN_ID` on the original run. Do not create a replacement run in the same source generation. Source coverage/readiness failures require backend preparation, then a new create attempt.
5. **Review.** Read the [review policy](references/review-policy.md). Fetch `review-queue RUN_ID --output FILE --offset OFFSET --limit 50` from offset 0, following `next_offset` until `next_offset` is null. Review every `source_item` plus `context_items`; merge all page decisions, then submit once with `apply-overrides RUN_ID --file FILE`. Never submit only page one.
6. **Verify.** Run `verify RUN_ID`. Completion requires zero data coverage gaps and stale-index violations, exact classification coverage, verified source evidence, and valid links; otherwise report incomplete.
7. **Export.** Run `export RUN_ID --format xlsx|jsonl`, then `download RUN_ID ARTIFACT --output FILE` with the returned artifact name.
8. **Deliver.** Provide the artifact, run ID, cutoff, counts, limitations, `result_scope`, `returned_feedback`, and `possibly_more_matches`. Use returned `result_scope=reviewed` or `result_scope=representative`; never invent a result_scope value. Representative is not complete. Do not write formal labels or tune retrieval internals; vector similarity is recall evidence, not a final decision.
