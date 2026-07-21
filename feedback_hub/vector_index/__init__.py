"""SQLite metadata support for the local feedback vector index."""

from .config import VectorIndexConfig
from .models import EmbeddingRecord, PendingFeedback, ShardMetadata, VectorIndexStatus
from .repository import VectorRepository

__all__ = [
    "EmbeddingRecord",
    "PendingFeedback",
    "ShardMetadata",
    "VectorIndexConfig",
    "VectorIndexStatus",
    "VectorRepository",
]
