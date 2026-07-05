"""FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.v1.routes import router as v1_router
from app.config import settings

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    debug=settings.debug,
)

# Static files (frontend assets)
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# API routes
app.include_router(v1_router, prefix="/api/v1")


@app.get("/health")
def health_check():
    return {"status": "ok"}
