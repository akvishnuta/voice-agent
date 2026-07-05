"""Smoke tests for the FastAPI app."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_check():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_agent_parse_no_session():
    """Verify that querying a non-existent session returns 404."""
    resp = client.get("/api/v1/agent/status/nonexistent")
    assert resp.status_code == 404


def test_agent_parse_validation():
    """Verify parse endpoint rejects empty input."""
    resp = client.post("/api/v1/agent/parse", json={"text": ""})
    assert resp.status_code == 422


def test_app_page_served():
    """Verify the frontend HTML page is served."""
    resp = client.get("/api/v1/app")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
