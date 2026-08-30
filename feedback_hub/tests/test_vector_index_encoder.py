"""Qwen embedding encoder contract tests without vector runtime dependencies."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from feedback_hub.vector_index.config import VectorIndexConfig
from feedback_hub.vector_index.encoder import QwenEmbeddingEncoder, instruct_query


class FakeRuntime:
    def __init__(self) -> None:
        self.output = np.zeros((1, 1024), dtype=np.float64)
        self.calls: list[tuple[list[str], int, int]] = []

    def encode(
        self, texts: list[str], *, batch_size: int, max_length: int
    ) -> np.ndarray:
        self.calls.append((texts, batch_size, max_length))
        return self.output


@pytest.fixture
def config(tmp_path) -> VectorIndexConfig:
    return replace(VectorIndexConfig(), model_dir=tmp_path / "model", batch_size=3, max_length=77)


@pytest.fixture
def fake_runtime() -> FakeRuntime:
    return FakeRuntime()


def test_instruct_query_is_stable():
    assert instruct_query("工具栏不隐藏") == (
        "Instruct: Given a user feedback search query for the WeChat keyboard/input method product, "
        "retrieve relevant user feedback.\nQuery:工具栏不隐藏"
    )


def test_encoder_converts_runtime_output_to_float32(fake_runtime, config):
    result = QwenEmbeddingEncoder(config, runtime=fake_runtime).encode_documents(["正文"])

    assert result.shape == (1, 1024)
    assert result.dtype == np.float32
    assert fake_runtime.calls == [(["正文"], 3, 77)]


def test_encoder_formats_query_before_passing_to_runtime(fake_runtime, config):
    QwenEmbeddingEncoder(config, runtime=fake_runtime).encode_queries(["工具栏不隐藏"])

    assert fake_runtime.calls[0][0] == [instruct_query("工具栏不隐藏")]


def test_encoder_rejects_wrong_dimension(fake_runtime, config):
    fake_runtime.output = np.zeros((1, 8), dtype=np.float32)

    with pytest.raises(ValueError, match="dimension"):
        QwenEmbeddingEncoder(config, runtime=fake_runtime).encode_documents(["正文"])


def test_encoder_rejects_non_finite_output(fake_runtime, config):
    fake_runtime.output = np.full((1, 1024), np.nan, dtype=np.float32)

    with pytest.raises(ValueError, match="non-finite"):
        QwenEmbeddingEncoder(config, runtime=fake_runtime).encode_documents(["正文"])


def test_encoder_returns_empty_matrix_without_loading_runtime(fake_runtime, config):
    result = QwenEmbeddingEncoder(config, runtime=fake_runtime).encode_documents([])

    assert result.shape == (0, 1024)
    assert result.dtype == np.float32
    assert fake_runtime.calls == []


def test_encoder_rejects_wrong_number_of_vectors(fake_runtime, config):
    fake_runtime.output = np.zeros((2, 1024), dtype=np.float32)

    with pytest.raises(ValueError, match="count"):
        QwenEmbeddingEncoder(config, runtime=fake_runtime).encode_documents(["正文"])
