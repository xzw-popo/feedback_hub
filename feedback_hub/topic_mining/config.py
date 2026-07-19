"""Configuration for the isolated topic-mining runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from feedback_hub import config as feedback_config


@dataclass(frozen=True)
class TopicMiningConfig:
    source_db_path: Path = feedback_config.DB_PATH
    data_dir: Path = Path(os.environ.get(
        "TOPIC_MINING_DATA_DIR", str(feedback_config.DATA_DIR / "topic_mining")
    ))
    vector_api_url: str = os.environ.get("TOPIC_VECTOR_API_URL", "")
    vector_api_token: str = os.environ.get("TOPIC_VECTOR_API_TOKEN", "")
    vector_index: str = os.environ.get("TOPIC_VECTOR_INDEX", "feedback-items-v1")
    vector_max_lag_seconds: int = int(os.environ.get("TOPIC_VECTOR_MAX_LAG_SECONDS", "21600"))
    api_token: str = os.environ.get("TOPIC_MINING_API_TOKEN", "")
    bm25_top_k: int = 1000
    vector_top_k: int = 1000
    candidate_limit: int = 1500
    rrf_k: int = 60
    classifier_batch_size: int = 20
    classifier_concurrency: int = 8
