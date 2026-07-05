"""API v1 routes."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/hello")
def hello():
    """Sample endpoint."""
    return {"message": "Hello from Project AI"}
