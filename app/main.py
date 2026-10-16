"""PetCare Buddy - FastAPI application entry point.

Run:  uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.routes import booking, cat, chat, services, vision
from app.state import build_state, get_state

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # A failed start must not take the whole function down: log it, and let the
    # first request try again (get_state) so the error shows in the host's logs.
    try:
        app.state.petcare = build_state()
    except Exception:  # pragma: no cover - only on a broken host
        logging.getLogger("petcare").exception("Startup failed; will retry on the first request")
    yield


app = FastAPI(
    title="PetCare Buddy API",
    description="Intelligent chatbot for PetCare Grooming (fictional business) - RAG + Vision + Guardrails",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(chat.router)
app.include_router(services.router)
app.include_router(vision.router)
app.include_router(booking.router)
app.include_router(cat.router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "invalid_request",
                "message": "ข้อมูลที่ส่งมาไม่ถูกต้อง กรุณาตรวจสอบข้อความหรือไฟล์อีกครั้ง",
                "errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()],
            }
        },
    )


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin", include_in_schema=False)
def admin_page():
    """Staff page for confirming or rejecting booking requests (needs ADMIN_TOKEN)."""
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/dashboard", include_in_schema=False)
def dashboard_page():
    """Staff dashboard: daily appointment table, request ids per day and booking overview (needs ADMIN_TOKEN)."""
    return FileResponse(STATIC_DIR / "dashboard.html")


@app.get("/health", tags=["system"])
def health(request: Request):
    s = get_state(request)
    return {
        "status": "ok",
        "knowledge_base": {
            "services": len(s.kb.services),
            "policies": len(s.kb.policies),
            "faq": len(s.kb.faq),
            "chunks": len(s.store.chunks),
            "drafts_included": s.kb.drafts_included,
        },
        "vector_store": {
            "engine": "faiss.IndexFlatIP",
            "embedder": s.store.embedder.name,
            "dimension": s.store.index.d,
            "fingerprint": s.store.fingerprint,
            "loaded_from_disk": s.store.loaded_from_disk,
        },
        "llm": {
            "provider": s.settings.llm_provider,
            "model": s.settings.llm_model,
            "configured": s.llm.available,
        },
        "vision": {"available": s.llm.available, "max_image_mb": s.settings.max_image_mb},
        "cat_estimate": {"available": s.llm.available, "pricing_file": s.settings.cat_pricing_path.name},
        "booking": {"storage": "sqlite", "staff_page_enabled": bool(s.settings.admin_token)},
        "startup_ms": round(s.startup_ms, 1),
    }
