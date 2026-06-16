from __future__ import annotations

from fastapi.testclient import TestClient

from feedback_hub.api import create_app


def test_serves_frontend_index_when_dist_is_configured(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<main>Feedback Hub</main>", encoding="utf-8")

    client = TestClient(create_app(frontend_dist=str(dist)))

    resp = client.get("/")

    assert resp.status_code == 200
    assert "Feedback Hub" in resp.text


def test_frontend_routes_fallback_to_index(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<main>Feedback Hub</main>", encoding="utf-8")

    client = TestClient(create_app(frontend_dist=str(dist)))

    resp = client.get("/conversations/abc")

    assert resp.status_code == 200
    assert "Feedback Hub" in resp.text
