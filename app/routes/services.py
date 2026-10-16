"""Read-only Knowledge Base endpoints: services, FAQ, policies, retrieval preview."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query

from app.state import AppState, get_state

router = APIRouter(prefix="/api", tags=["knowledge"])


@router.get("/services")
def list_services(
    pet_type: str | None = Query(None, description="กรองตามชนิดสัตว์ เช่น สุนัข หรือ แมว"),
    state: AppState = Depends(get_state),
):
    services = state.kb.services
    if pet_type:
        services = [s for s in services if pet_type in s.pet_type]
    return {"count": len(services), "currency": "THB", "services": [s.model_dump() for s in services]}


@router.get("/services/{service_id}")
def get_service(service_id: str, state: AppState = Depends(get_state)):
    sid = service_id.strip().upper()
    svc = next((s for s in state.kb.services if s.id == sid), None)
    if svc is None:
        raise HTTPException(404, detail={"code": "service_not_found", "message": f"ไม่พบบริการรหัส {sid}"})
    return svc.model_dump()


@router.get("/faq")
def list_faq(state: AppState = Depends(get_state)):
    return {"count": len(state.kb.faq), "faq": [f.model_dump() for f in state.kb.faq]}


@router.get("/policies")
def list_policies(state: AppState = Depends(get_state)):
    return {"count": len(state.kb.policies), "policies": [p.model_dump() for p in state.kb.policies]}


@router.get("/retrieve")
def retrieve_preview(
    q: str = Query(..., min_length=1, max_length=500, description="คำถามสำหรับทดสอบการค้นคืน"),
    k: int = Query(5, ge=1, le=10),
    state: AppState = Depends(get_state),
):
    """Shows exactly which Knowledge Base chunks the RAG step would pass to the LLM."""
    t0 = time.perf_counter()
    results = state.retriever.retrieve(q, k=k)
    return {
        "query": q,
        "embedder": state.store.embedder.name,
        "results": [
            {"id": r.chunk.id, "title": r.chunk.title, "score": round(r.score, 4), "text": r.chunk.text}
            for r in results
        ],
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
