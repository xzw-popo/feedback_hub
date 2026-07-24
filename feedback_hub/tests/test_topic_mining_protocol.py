from feedback_hub.topic_mining.protocol import (
    classification_capability,
    classification_protocol,
)


def test_classification_capability_is_caller_ai_v3():
    assert classification_capability() == {
        "version": 3,
        "owner": "caller_ai",
        "candidate_page_default": 20,
        "candidate_page_maximum": 20,
        "matched_evidence": "exact_candidate_substring",
        "partial_acceptance": True,
    }


def test_classification_protocol_defaults_legacy_runs_to_backend_model():
    assert classification_protocol({}) == (1, "backend_model")
    assert classification_protocol({
        "classification_protocol_version": 2,
        "classification_owner": "caller_ai",
    }) == (2, "caller_ai")
    assert classification_protocol({
        "classification_protocol_version": 3,
        "classification_owner": "caller_ai",
    }) == (3, "caller_ai")
