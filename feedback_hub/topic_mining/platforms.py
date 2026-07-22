"""Canonical platform metadata for topic-mining hard scope."""

from __future__ import annotations

from typing import Any, Sequence


CANONICAL_PLATFORMS = ("Win", "Android", "iOS", "Mac")

PLATFORM_ALIASES = {
    "Win": "Win",
    "Windows": "Win",
    "Win端": "Win",
    "Windows端": "Win",
    "Android": "Android",
    "安卓": "Android",
    "Android端": "Android",
    "安卓端": "Android",
    "iOS": "iOS",
    "iOS端": "iOS",
    "Mac": "Mac",
    "macOS": "Mac",
    "Mac端": "Mac",
    "macOS端": "Mac",
}


def _alias_key(value: str) -> str:
    return "".join(value.split()).casefold()


_CANONICAL_BY_ALIAS = {
    _alias_key(alias): canonical
    for alias, canonical in PLATFORM_ALIASES.items()
}


def normalize_platforms(values: Sequence[str]) -> tuple[str, ...]:
    """Return canonical, first-occurrence-deduplicated platform values."""
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        canonical = _CANONICAL_BY_ALIAS.get(_alias_key(value))
        if canonical is None:
            supported = ", ".join(CANONICAL_PLATFORMS)
            raise ValueError(
                f"unsupported platform: {value}; supported platforms: {supported}"
            )
        if canonical not in seen:
            seen.add(canonical)
            normalized.append(canonical)
    return tuple(normalized)


def platform_capability() -> dict[str, Any]:
    """Serialize the public platform normalization contract."""
    return {
        "canonical_values": list(CANONICAL_PLATFORMS),
        "aliases": dict(PLATFORM_ALIASES),
        "matching": "case_insensitive_ignore_whitespace",
    }
