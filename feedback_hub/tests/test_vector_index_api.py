"""HTTP v1 contract for the localhost exact vector service."""
from __future__ import annotations

from fastapi.testclient import TestClient

from feedback_hub.topic_mining.config import TopicMiningConfig
from feedback_hub.topic_mining.vector_client import HttpVectorSearchClient
from feedback_hub.vector_index.api import create_app
import pytest
from feedback_hub.tests.test_vector_index_search import indexed as _indexed


@pytest.fixture
def indexed(tmp_path):
    yield from _indexed.__wrapped__(tmp_path)


def _request_adapter(test_client):
    def request(method, url, **kwargs):
        path = url.removeprefix("http://vector.test")
        return test_client.request(method, path, params=kwargs.get("params"), json=kwargs.get("json"))
    return request


def test_api_matches_existing_vector_client(indexed):
    config, _, _ = indexed
    from feedback_hub.tests.test_vector_index_search import StubEncoder
    test_client = TestClient(create_app(config, encoder=StubEncoder()))
    topic_config = TopicMiningConfig(data_dir=config.data_dir, vector_api_url="http://vector.test", vector_index=config.index_name)
    client = HttpVectorSearchClient(topic_config, request_fn=_request_adapter(test_client))

    assert client.capabilities().supported_units == ("feedback",)
    result = client.search([{"id": "q1", "text": "工具栏", "kind": "positive"}], {"unit": "feedback"}, 10)
    assert result.index == config.index_name
    assert result.watermark_ts_ms == 777
    assert result.hits


def test_api_rejects_unknown_index_and_redacts_internal_failures(indexed):
    config, _, _ = indexed
    from feedback_hub.tests.test_vector_index_search import StubEncoder
    test_client = TestClient(create_app(config, encoder=StubEncoder()), raise_server_exceptions=False)

    assert test_client.get("/capabilities", params={"index": "other"}).status_code == 404
    response = test_client.post("/search", json={"index": config.index_name, "queries": [], "filters": {"path": "/secret"}, "limit": 1})
    assert response.status_code == 422
    assert str(config.data_dir) not in response.text
    assert "Traceback" not in response.text


def test_health_reports_ready_index_and_watermark(indexed):
    config, _, _ = indexed
    from feedback_hub.tests.test_vector_index_search import StubEncoder
    client = TestClient(create_app(config, encoder=StubEncoder()))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["ready"] is True
    assert response.json()["watermark_ts_ms"] == 777
