---
name: mining-feedback-topics
description: Use when a user needs to mine, collect, or audit a one-off feedback issue or topic from internal feedback data, especially when strict inclusion/exclusion boundaries, semantic recall, source evidence, links, or a downloadable result are required.
---

# Mining Feedback Topics

Let the backend execute reliable data work; use AI judgment only to define the topic and assess semantic boundaries.

Use only the bundled backend client. Do not query source databases or vector tools directly. When asked only to explain a plan, do not claim artifacts, results, or validation. Do not expand the user's named object into a different object or product. Keep each user-named target object and behavior as required inclusion conditions; add only meaning-preserving paraphrases. Put unrequested adjacent objects or behaviors only in exclusion criteria.

A blocked pre-run response contains four slots: proposed inclusion, proposed exclusion, one material scope question when needed, and the service or configuration blocker. Treat an explicit relative time range as supplied scope; resolve it from the current date and timezone without asking again. If start or end time is missing, ask the single time-range question directly: “What start time, end time, and timezone should this run use?” Never default to all history. Do not omit the topic boundary merely because the backend is unavailable.

1. **Capabilities.** Run [topic_backend_client.py](scripts/topic_backend_client.py) with `capabilities` before promising coverage. Confirm schema versions, formats, statuses, and read-only flags.
2. **Validate.** Read the [topic spec](references/topic-spec.md). Convert the request to one topic spec. Populate each hard-scope field only with values explicit in the current request; leave every other hard-scope field empty. Ask one question only if a missing scope choice materially changes the result. Run [validate_topic_spec.py](scripts/validate_topic_spec.py) with `TOPIC_SPEC_PATH` and repair every error.
3. **Create.** Submit the validated spec with `create-run --spec FILE`. Reuse the returned ID; never create a replacement while that run is running.
4. **Inspect.** Read the [backend contract](references/backend-contract.md). Copy client commands from the backend contract verbatim. Poll `get-run RUN_ID`; inspect funnel counts, data cutoff, vector watermark, and unresolved failures. For `paused_quota_exhausted`, repair quota/service access and run `resume RUN_ID` on the original run. For a repairable `failed` run, repair the reported service or configuration failure, then resume the original run. Do not create a replacement run.
5. **Review.** Read the [review policy](references/review-policy.md). Fetch only `review-queue RUN_ID --output FILE`; for every non-empty queue, review `source_item` plus `context_items` and submit one explicit decision per queue item with `apply-overrides RUN_ID --file FILE`.
6. **Verify.** Run `verify RUN_ID` before requesting output. A complete result has zero data coverage gaps, zero stale-index violations, exact classification coverage, verified source evidence, and valid links. Otherwise report it as incomplete.
7. **Export.** Run `export RUN_ID --format xlsx|jsonl`, then `download RUN_ID ARTIFACT --output FILE` using the returned artifact name.
8. **Deliver.** Provide the requested artifact and state its run ID, cutoff, counts, and limitations. Do not write formal labels or tune retrieval internals; vector similarity is recall evidence, not a final decision.
