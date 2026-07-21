"""CPU Qwen embedding adapter for the feedback vector index."""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, Sequence

import numpy as np

from .config import VectorIndexConfig

if TYPE_CHECKING:
    from torch import Tensor


QUERY_TASK = (
    "Given a user feedback search query for the WeChat keyboard/input method product, "
    "retrieve relevant user feedback."
)
EMBEDDING_DIMENSION = 1024


class EmbeddingEncoder(Protocol):
    """Generates normalized float32 document and query embedding matrices."""

    def encode_documents(self, texts: Sequence[str]) -> np.ndarray:
        """Encode feedback text as one 1024-dimensional vector per input."""

    def encode_queries(self, texts: Sequence[str]) -> np.ndarray:
        """Encode search text as one 1024-dimensional vector per input."""


class _EmbeddingRuntime(Protocol):
    def encode(
        self, texts: Sequence[str], *, batch_size: int, max_length: int
    ) -> np.ndarray:
        """Encode texts using the configured model runtime."""


def last_token_pool(last_hidden_states: Tensor, attention_mask: Tensor) -> Tensor:
    """Pool the final non-padding token for left- or right-padded batches."""
    import torch

    if attention_mask[:, -1].sum() == attention_mask.shape[0]:
        return last_hidden_states[:, -1]
    sequence_lengths = attention_mask.sum(dim=1) - 1
    return last_hidden_states[
        torch.arange(last_hidden_states.shape[0], device=last_hidden_states.device),
        sequence_lengths,
    ]


def instruct_query(query: str) -> str:
    """Format a query with the model's stable retrieval instruction."""
    return f"Instruct: {QUERY_TASK}\nQuery:{query}"


class _QwenRuntime:
    """Lazily imported PyTorch/Transformers runtime pinned to CPU."""

    def __init__(self, model_dir: str) -> None:
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "Qwen vector dependencies are unavailable. Install requirements-vector.txt "
                "and CPU PyTorch before constructing QwenEmbeddingEncoder."
            ) from error

        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(model_dir)
        self._model = AutoModel.from_pretrained(model_dir)
        self._model.to("cpu")
        self._model.eval()

    def encode(
        self, texts: Sequence[str], *, batch_size: int, max_length: int
    ) -> np.ndarray:
        matrices: list[np.ndarray] = []
        for start in range(0, len(texts), batch_size):
            batch = list(texts[start : start + batch_size])
            tokens = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=max_length,
                return_tensors="pt",
            )
            with self._torch.inference_mode():
                output = self._model(**tokens)
                pooled = last_token_pool(output.last_hidden_state, tokens["attention_mask"])
                normalized = self._torch.nn.functional.normalize(pooled, p=2, dim=1)
            matrices.append(normalized.to(dtype=self._torch.float32).cpu().numpy())
        return np.concatenate(matrices, axis=0)


class QwenEmbeddingEncoder:
    """Qwen3-Embedding-0.6B adapter for documents and instructed queries."""

    def __init__(
        self, config: VectorIndexConfig, *, runtime: _EmbeddingRuntime | None = None
    ) -> None:
        self.config = config
        self._runtime = runtime

    def encode_documents(self, texts: Sequence[str]) -> np.ndarray:
        """Encode feedback bodies without a query instruction."""
        return self._encode(texts)

    def encode_queries(self, texts: Sequence[str]) -> np.ndarray:
        """Encode query text with the Qwen retrieval instruction."""
        return self._encode([instruct_query(text) for text in texts])

    def _encode(self, texts: Sequence[str]) -> np.ndarray:
        values = list(texts)
        if not values:
            return np.empty((0, EMBEDDING_DIMENSION), dtype=np.float32)

        runtime = self._runtime
        if runtime is None:
            runtime = _QwenRuntime(str(self.config.model_dir))
            self._runtime = runtime
        matrix = np.asarray(
            runtime.encode(
                values,
                batch_size=self.config.batch_size,
                max_length=self.config.max_length,
            ),
            dtype=np.float32,
        )
        if matrix.ndim != 2 or matrix.shape[0] != len(values):
            raise ValueError("embedding count does not match input count")
        if matrix.shape[1] != EMBEDDING_DIMENSION:
            raise ValueError(
                f"embedding dimension must be {EMBEDDING_DIMENSION}, got {matrix.shape[1]}"
            )
        if not np.isfinite(matrix).all():
            raise ValueError("embedding matrix contains non-finite values")
        return matrix
