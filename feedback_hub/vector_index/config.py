"""Configuration defaults for the local feedback vector index."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from feedback_hub import config as feedback_config


@dataclass(frozen=True)
class VectorIndexConfig:
    """Static configuration for a feedback-level Qwen vector index."""

    db_path: Path = feedback_config.DB_PATH
    data_dir: Path = feedback_config.DATA_DIR / "vector_index"
    model_dir: Path = feedback_config.DATA_DIR / "models" / "Qwen3-Embedding-0.6B"
    model_version: str = "qwen3-embedding-0.6b-document-v1"
    index_name: str = "feedback-items-v1"
    dimension: int = 1024
    batch_size: int = 8
    max_length: int = 512
    shard_size: int = 2048
    compact_after_shards: int = 72
    host: str = "127.0.0.1"
    port: int = 8011
