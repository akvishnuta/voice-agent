"""FastAPI application entry point."""

from fastapi import FastAPI

from app.api.v1.routes import router as v1_router
from app.config import settings

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.debug,
)

app.include_router(v1_router, prefix="/api/v1")


@app.get("/health")
def health_check():
    return {"status": "ok"}
