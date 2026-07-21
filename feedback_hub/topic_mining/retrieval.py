"""Deterministic, auditable BM25 and vector-service recall for topic mining."""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .config import TopicMiningConfig
from .contracts import TopicSpec
from .vector_client import VectorHit


_ASCII_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_+.#-]*")
_CHINESE_RE = re.compile(r"[\u4e00-\u9fff]+")
_DOMAIN_TERMS = tuple(sorted({
    "airpods", "mac", "windows", "android", "ios", "ui", "语音输入", "语音识别", "语音转文字",
    "隔空传送", "文字整理", "快捷发送", "自定义快捷键", "候选词", "联想词", "预测词", "推荐词",
    "快捷键", "麦克风", "输入法", "微信输入法", "微信键盘", "听歌", "音乐", "歌曲", "音频",
    "播放", "视频", "声音", "音量", "静音", "识别", "听写", "语音", "输入", "网络", "联网",
    "断网", "界面", "设计", "显示", "皮肤", "主题", "热键",
}, key=lambda term: (-len(term), term)))
_CONFIGURATION_VERSION = 1


class VectorIndexStaleError(ValueError):
    """Raised when vector recall does not cover the source snapshot watermark."""


@dataclass(frozen=True)
class BM25Hit:
    index: int
    score: float


@dataclass(frozen=True)
class RecallHit:
    item_id: str
    item: dict[str, Any]
    channels: tuple[str, ...]
    fused_score: float
    fused_rank: int
    channel_ranks: dict[str, int]
    raw_scores: dict[str, float]
    query_ids: tuple[str, ...]
    negative_query_hits: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "item": self.item,
            "channels": list(self.channels),
            "fused_score": self.fused_score,
            "fused_rank": self.fused_rank,
            "channel_ranks": self.channel_ranks,
            "raw_scores": self.raw_scores,
            "query_ids": list(self.query_ids),
            "negative_query_hits": list(self.negative_query_hits),
        }


@dataclass(frozen=True)
class RecallPlan:
    candidates: tuple[RecallHit, ...]
    retrieved_candidate_count: int
    rejected_out_of_scope_ids: tuple[str, ...]
    semantic_queries: tuple[dict[str, str], ...]
    bm25_query: str
    source_watermark_ms: int | None
    vector_watermark_ms: int | None


def tokenize_text(text: str) -> list[str]:
    """Tokenize in the stable manner established by the embedding lab."""
    lowered = str(text).lower()
    tokens = [match.group(0) for match in _ASCII_RE.finditer(lowered)]
    tokens.extend(term for term in _DOMAIN_TERMS if term in lowered)
    for segment in _CHINESE_RE.findall(lowered):
        if len(segment) == 1:
            tokens.append(segment)
        else:
            tokens.extend(segment[index:index + 2] for index in range(len(segment) - 1))
    return tokens


class BM25Index:
    def __init__(self, *, inverted: dict[str, list[tuple[int, int]]], doc_lengths: list[int], avg_doc_length: float, doc_count: int, k1: float = 1.5, b: float = 0.75) -> None:
        self.inverted = inverted
        self.doc_lengths = doc_lengths
        self.avg_doc_length = avg_doc_length
        self.doc_count = doc_count
        self.k1 = k1
        self.b = b

    @classmethod
    def from_texts(cls, texts: Iterable[str], *, k1: float = 1.5, b: float = 0.75) -> "BM25Index":
        counters = [Counter(tokenize_text(text)) for text in texts]
        inverted: dict[str, list[tuple[int, int]]] = defaultdict(list)
        lengths: list[int] = []
        for index, counter in enumerate(counters):
            lengths.append(sum(counter.values()))
            for term, frequency in counter.items():
                inverted[term].append((index, frequency))
        return cls(
            inverted=dict(inverted), doc_lengths=lengths,
            avg_doc_length=sum(lengths) / len(lengths) if lengths else 0.0,
            doc_count=len(lengths), k1=k1, b=b,
        )

    def search(self, query: str, *, top_k: int) -> list[BM25Hit]:
        if top_k <= 0 or not self.doc_count or not self.avg_doc_length:
            return []
        scores: dict[int, float] = defaultdict(float)
        for term, query_frequency in Counter(tokenize_text(query)).items():
            postings = self.inverted.get(term)
            if not postings:
                continue
            document_frequency = len(postings)
            idf = math.log(1.0 + (self.doc_count - document_frequency + 0.5) / (document_frequency + 0.5))
            for index, term_frequency in postings:
                denominator = term_frequency + self.k1 * (1.0 - self.b + self.b * self.doc_lengths[index] / self.avg_doc_length)
                scores[index] += query_frequency * idf * (term_frequency * (self.k1 + 1.0) / denominator)
        return [BM25Hit(index, score) for index, score in sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))[:top_k]]


def build_semantic_queries(spec: TopicSpec) -> list[dict[str, str]]:
    queries = [{"id": "objective:0", "text": spec.objective, "kind": "positive"}]
    queries.extend({"id": f"positive:{index}", "text": text, "kind": "positive"} for index, text in enumerate(spec.positive_examples))
    queries.extend({"id": f"negative:{index}", "text": text, "kind": "negative"} for index, text in enumerate(spec.negative_examples))
    return queries


def build_bm25_query(spec: TopicSpec) -> str:
    hints = [hint for name in sorted(spec.lexical_hints) for hint in spec.lexical_hints[name]]
    return "\n".join((spec.objective, *spec.inclusion_criteria, *hints))


def build_vector_filters(spec: TopicSpec) -> dict[str, Any]:
    start_ts_ms = int(spec.scope.start_time.astimezone(timezone.utc).timestamp() * 1000)
    end_ts_ms = int(spec.scope.end_time.astimezone(timezone.utc).timestamp() * 1000)
    filters: dict[str, Any] = {"unit": spec.unit, "start_ts_ms": start_ts_ms, "end_ts_ms": end_ts_ms}
    for field in ("platforms", "products", "channels", "versions"):
        values = getattr(spec.scope, field)
        if values:
            filters[field] = list(values)
    return filters


def recall_budget(spec: TopicSpec, config: TopicMiningConfig) -> tuple[int, int]:
    """Return the backend-owned channel depth and total recall pool for a mode."""
    if spec.mode == "standard":
        return config.standard_channel_top_k, config.standard_recall_pool_limit
    return config.exhaustive_channel_top_k, config.exhaustive_recall_pool_limit


def verify_vector_watermark(*, source_watermark_ms: int, vector_watermark_ms: int | None, max_lag_seconds: int) -> None:
    if vector_watermark_ms is None:
        raise VectorIndexStaleError("vector index watermark is missing")
    if vector_watermark_ms < source_watermark_ms - max_lag_seconds * 1000:
        raise VectorIndexStaleError(
            f"vector index watermark {vector_watermark_ms} is too stale for source watermark {source_watermark_ms}"
        )


def _validate_items(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        item_id = item.get("item_id")
        if not isinstance(item_id, str) or not item_id:
            raise ValueError("scoped item is missing item_id")
        if item_id in seen:
            raise ValueError(f"scoped items have duplicate item_id: {item_id}")
        seen.add(item_id)
        text = item.get("text")
        if not isinstance(text, str):
            raise ValueError(f"scoped item {item_id} is missing text")
        normalized.append(dict(item))
    return sorted(normalized, key=lambda item: item["item_id"])


def _write_audit_artifacts(plan: RecallPlan, artifact_dir: Path, config: TopicMiningConfig) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    candidate_path = artifact_dir / "recall_candidates.jsonl"
    candidate_path.write_text("".join(json.dumps(hit.to_dict(), ensure_ascii=False, sort_keys=True) + "\n" for hit in plan.candidates), encoding="utf-8")
    config_values = asdict(config)
    config_values["source_db_path"] = str(config_values["source_db_path"])
    config_values["data_dir"] = str(config_values["data_dir"])
    config_values.pop("vector_api_token", None)
    config_values.pop("api_token", None)
    manifest = {
        "configuration_version": _CONFIGURATION_VERSION,
        "source_watermark_ms": plan.source_watermark_ms,
        "vector_watermark_ms": plan.vector_watermark_ms,
        "bm25_query": plan.bm25_query,
        "semantic_queries": list(plan.semantic_queries),
        "rejected_out_of_scope_ids": list(plan.rejected_out_of_scope_ids),
        "retrieved_candidate_count": plan.retrieved_candidate_count,
        "recall_pool_count": len(plan.candidates),
        "candidate_count": len(plan.candidates),
        "config": config_values,
    }
    (artifact_dir / "recall_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def hybrid_recall(
    items: Sequence[Mapping[str, Any]],
    spec: TopicSpec,
    *,
    vector_hits: Sequence[VectorHit] = (),
    config: TopicMiningConfig,
    artifact_dir: Path | None = None,
    source_watermark_ms: int | None = None,
    vector_watermark_ms: int | None = None,
) -> RecallPlan:
    """Fuse deterministic lexical recall with vector recall, never relaxing scope.

    Returned candidates are only recall candidates; no final-match claim is made
    at this stage.
    """
    if source_watermark_ms is not None:
        verify_vector_watermark(
            source_watermark_ms=source_watermark_ms,
            vector_watermark_ms=vector_watermark_ms,
            max_lag_seconds=config.vector_max_lag_seconds,
        )
    source_items = _validate_items(items)
    item_by_id = {item["item_id"]: item for item in source_items}
    channel_top_k, recall_pool_limit = recall_budget(spec, config)
    bm25_query = build_bm25_query(spec)
    bm25_hits = BM25Index.from_texts(item["text"] for item in source_items).search(bm25_query, top_k=channel_top_k)
    ranks: dict[str, dict[str, int]] = defaultdict(dict)
    scores: dict[str, dict[str, float]] = defaultdict(dict)
    query_ids: dict[str, set[str]] = defaultdict(set)
    negative_hits: dict[str, set[str]] = defaultdict(set)
    channels: dict[str, set[str]] = defaultdict(set)
    for rank, hit in enumerate(bm25_hits, start=1):
        item_id = source_items[hit.index]["item_id"]
        ranks[item_id]["bm25"] = rank
        scores[item_id]["bm25"] = hit.score
        query_ids[item_id].add("bm25")
        channels[item_id].add("bm25")
    rejected_out_of_scope: set[str] = set()
    vector_query_kinds = {query["id"]: query["kind"] for query in build_semantic_queries(spec)}
    for hit in vector_hits:
        query_kind = vector_query_kinds.get(hit.query_id)
        if query_kind is None:
            raise ValueError(f"unknown vector query_id: {hit.query_id}")
        if hit.item_id not in item_by_id:
            rejected_out_of_scope.add(hit.item_id)
            continue
        if hit.rank > channel_top_k:
            continue
        if query_kind == "negative":
            negative_hits[hit.item_id].add(hit.query_id)
            continue
        ranks[hit.item_id][hit.query_id] = hit.rank
        scores[hit.item_id][hit.query_id] = hit.score
        query_ids[hit.item_id].add(hit.query_id)
        channels[hit.item_id].add("vector")
    candidates: list[RecallHit] = []
    for item_id in sorted(channels):
        positive_ranks = ranks[item_id]
        fused_score = sum(1.0 / (config.rrf_k + rank) for rank in positive_ranks.values())
        candidates.append(RecallHit(
            item_id=item_id,
            item=item_by_id[item_id],
            channels=tuple(sorted(channels[item_id])),
            fused_score=fused_score,
            fused_rank=0,
            channel_ranks=dict(sorted(positive_ranks.items())),
            raw_scores=dict(sorted(scores[item_id].items())),
            query_ids=tuple(sorted(query_ids[item_id])),
            negative_query_hits=tuple(sorted(negative_hits[item_id])),
        ))
    candidates.sort(key=lambda hit: (-hit.fused_score, hit.item_id))
    ranked_candidates = tuple(
        replace(hit, fused_rank=rank)
        for rank, hit in enumerate(candidates, start=1)
    )
    plan = RecallPlan(
        candidates=ranked_candidates[:recall_pool_limit],
        retrieved_candidate_count=len(ranked_candidates),
        rejected_out_of_scope_ids=tuple(sorted(rejected_out_of_scope)),
        semantic_queries=tuple(build_semantic_queries(spec)),
        bm25_query=bm25_query,
        source_watermark_ms=source_watermark_ms,
        vector_watermark_ms=vector_watermark_ms,
    )
    if artifact_dir is not None:
        _write_audit_artifacts(plan, Path(artifact_dir), config)
    return plan
