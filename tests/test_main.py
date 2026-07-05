"""Smoke tests for the FastAPI app."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_check():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_hello():
    resp = client.get("/api/v1/hello")
    assert resp.status_code == 200
    assert resp.json() == {"message": "Hello from Project AI"}
