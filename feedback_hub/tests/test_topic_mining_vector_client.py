from __future__ import annotations

import math

import pytest

from feedback_hub.topic_mining.config import TopicMiningConfig
from feedback_hub.topic_mining.vector_client import (
    HttpVectorSearchClient,
    VectorResponseError,
)


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class FakeHttp:
    def __init__(self, responses: list[dict]):
        self.responses = iter(responses)
        self.method = ""
        self.url = ""
        self.json: dict | None = None
        self.params: dict | None = None
        self.headers: dict | None = None
        self.timeout: int | None = None

    def __call__(self, method, url, **kwargs):
        self.method = method
        self.url = url
        self.json = kwargs.get("json")
        self.params = kwargs.get("params")
        self.headers = kwargs.get("headers")
        self.timeout = kwargs.get("timeout")
        return FakeResponse(next(self.responses))


@pytest.fixture
def config(tmp_path):
    return TopicMiningConfig(
        data_dir=tmp_path,
        vector_api_url="https://vector.example.test/",
        vector_api_token="top-secret-token",
        vector_index="feedback-items-v1",
    )


def test_vector_client_sends_semantic_queries_and_metadata_scope(config):
    fake_http = FakeHttp([{
        "index": "feedback-items-v1",
        "watermark_ts_ms": 1784191200000,
        "hits": [{"item_id": "feedback-id", "query_id": "positive:0", "score": 0.81, "rank": 1}],
    }])
    client = HttpVectorSearchClient(config, request_fn=fake_http)

    result = client.search(
        queries=[{"id": "positive:0", "text": "全屏时工具栏不隐藏", "kind": "positive"}],
        filters={"unit": "feedback", "start_ts_ms": 1, "end_ts_ms": 9, "platforms": ["Win"]},
        limit=1000,
    )

    assert fake_http.method == "POST"
    assert fake_http.url == "https://vector.example.test/search"
    assert fake_http.json["index"] == "feedback-items-v1"
    assert fake_http.json["queries"][0]["kind"] == "positive"
    assert fake_http.json["filters"]["platforms"] == ["Win"]
    assert fake_http.timeout == 30
    assert fake_http.headers == {"Authorization": "Bearer top-secret-token"}
    assert result.hits[0].item_id == "feedback-id"
    assert result.watermark_ts_ms == 1784191200000


def test_vector_client_reads_supported_capabilities_without_token(config):
    config = TopicMiningConfig(
        data_dir=config.data_dir,
        vector_api_url=config.vector_api_url,
        vector_index=config.vector_index,
    )
    fake_http = FakeHttp([{
        "index": "feedback-items-v1",
        "schema_version": 1,
        "supported_units": ["feedback", "conversation"],
        "watermark_ts_ms": 1784191200000,
    }])

    capabilities = HttpVectorSearchClient(config, request_fn=fake_http).capabilities()

    assert fake_http.method == "GET"
    assert fake_http.params == {"index": "feedback-items-v1"}
    assert fake_http.headers == {}
    assert capabilities.supported_units == ("feedback", "conversation")


@pytest.mark.parametrize("payload", [
    {"index": "unknown", "schema_version": 1, "supported_units": ["feedback"], "watermark_ts_ms": 1},
    {"index": "feedback-items-v1", "schema_version": 2, "supported_units": ["feedback"], "watermark_ts_ms": 1},
    {"index": "feedback-items-v1", "schema_version": 1, "supported_units": ["feedback"], "watermark_ts_ms": None},
])
def test_vector_client_rejects_invalid_capabilities(config, payload):
    with pytest.raises(VectorResponseError):
        HttpVectorSearchClient(config, request_fn=FakeHttp([payload])).capabilities()


@pytest.mark.parametrize("hits", [
    [{"item_id": "a", "query_id": "positive:0", "score": 1.0, "rank": 1},
     {"item_id": "a", "query_id": "positive:0", "score": 0.5, "rank": 2}],
    [{"item_id": "", "query_id": "positive:0", "score": 1.0, "rank": 1}],
    [{"item_id": "a", "query_id": "positive:0", "score": math.inf, "rank": 1}],
    [{"item_id": "a", "query_id": "positive:0", "score": 1.0, "rank": 0}],
])
def test_vector_client_rejects_unsafe_search_hits_and_redacts_token(config, hits):
    fake_http = FakeHttp([{
        "index": "feedback-items-v1", "watermark_ts_ms": 1, "hits": hits,
    }])

    with pytest.raises(VectorResponseError) as error:
        HttpVectorSearchClient(config, request_fn=fake_http).search([], {}, 1)

    assert "top-secret-token" not in str(error.value)
