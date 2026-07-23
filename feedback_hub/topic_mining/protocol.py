"""Versioned classification protocol advertised by the topic-mining backend."""

from __future__ import annotations

from typing import Any, Mapping


CLASSIFICATION_PROTOCOL_VERSION = 2
CLASSIFICATION_OWNER = "caller_ai"
CANDIDATE_PAGE_LIMIT = 20


def classification_capability() -> dict[str, Any]:
    """Return the public contract for caller-owned classification."""
    return {
        "version": CLASSIFICATION_PROTOCOL_VERSION,
        "owner": CLASSIFICATION_OWNER,
        "candidate_page_default": CANDIDATE_PAGE_LIMIT,
        "candidate_page_maximum": CANDIDATE_PAGE_LIMIT,
        "matched_evidence": "exact_source_or_context_substring",
        "partial_acceptance": True,
    }


def classification_protocol(run: Mapping[str, Any]) -> tuple[int, str]:
    """Read protocol identity, defaulting legacy rows to backend-owned v1."""
    version = run.get("classification_protocol_version", 1)
    owner = run.get("classification_owner", "backend_model")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise ValueError("invalid classification protocol version")
    if not isinstance(owner, str) or not owner:
        raise ValueError("invalid classification owner")
    return version, owner
