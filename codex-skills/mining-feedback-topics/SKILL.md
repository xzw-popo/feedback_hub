---
name: mining-feedback-topics
description: Use when a user needs to mine, collect, or audit a one-off feedback issue or topic from internal feedback data, especially when strict inclusion/exclusion boundaries, semantic recall, source evidence, links, or a downloadable result are required.
---

# Mining Feedback Topics

Let the backend execute reliable data work; use AI judgment only to define the topic and assess semantic boundaries.

Use only the bundled backend client. Do not query source databases or vector tools directly. When asked only to explain a plan, do not claim artifacts, results, or validation. Do not expand the user's named object into a different object or product. Keep each user-named target object and behavior as required inclusion conditions; add only meaning-preserving paraphrases. Put unrequested adjacent objects or behaviors only in exclusion criteria.

A blocked pre-run response contains four slots: proposed inclusion, proposed exclusion, one material scope question when needed, and the service or configuration blocker. If start or end time is missing, ask the single time-range question; never default to all history. Do not omit the topic boundary merely because the backend is unavailable.

1. **Capabilities.** Run [topic_backend_client.py](scripts/topic_backend_client.py) with `capabilities` before promising coverage. Confirm read-only source, supported formats, and statuses.
2. **Validate.** Read the [topic spec](references/topic-spec.md). Convert the request to one topic spec. Populate each hard-scope field only with values explicit in the current request; leave every other hard-scope field empty. Ask one question only if a missing scope choice materially changes the result. Run [validate_topic_spec.py](scripts/validate_topic_spec.py) with `TOPIC_SPEC_PATH` and repair every error.
3. **Create.** Submit the validated spec with `create-run --spec TOPIC_SPEC_PATH`. Reuse the returned ID; never create a replacement while that run is running. Poll `get-run RUN_ID`.
4. **Inspect.** Read the [backend contract](references/backend-contract.md). Copy client commands from the backend contract verbatim rather than rewriting them from memory. Inspect funnel counts, data cutoff, vector watermark, and unresolved failures.
5. **Review.** Read the [review policy](references/review-policy.md). Inspect only the bounded `review-queue`; submit decisions as separate overrides.
6. **Verify.** Verify the run before requesting output. A complete result has zero data coverage gaps, zero stale-index violations, exact classification coverage, verified source evidence, and valid links. Otherwise report it as incomplete.
7. **Export.** Request the chosen export, then download only the returned artifact.
8. **Deliver.** Provide the requested artifact and state its run ID, cutoff, counts, and limitations. Do not write formal labels or tune retrieval internals; vector similarity is recall evidence, not a final decision.
