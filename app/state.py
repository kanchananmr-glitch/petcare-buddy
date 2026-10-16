"""Application state built once at startup: KB, vector index, LLM client."""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from fastapi import Request

from app.booking import BookingStore
from app.cat_estimate import CatPricing, knowledge_text, load_cat_pricing, price_numbers
from app.config import Settings, get_settings
from app.llm import LLMClient
from app.models import KnowledgeBase
from app.prompts import build_system_prompt
from app.rag.embeddings import make_embedder
from app.rag.loader import Chunk, build_chunks, load_knowledge_base
from app.rag.retriever import Retriever, VectorStore

log = logging.getLogger(__name__)


@dataclass
class AppState:
    settings: Settings
    kb: KnowledgeBase
    rules: dict
    store: VectorStore
    retriever: Retriever
    llm: LLMClient
    system_prompt: str
    bookings: BookingStore
    cat_pricing: CatPricing
    allowed_extra_prices: set[int]
    startup_ms: float


def build_state(settings: Settings | None = None) -> AppState:
    t0 = time.perf_counter()
    settings = settings or get_settings()
    kb = load_knowledge_base(settings.knowledge_path, settings.drafts_path, settings.include_drafts)
    rules = json.loads(settings.rules_path.read_text(encoding="utf-8"))
    cat_pricing = load_cat_pricing(settings.cat_pricing_path)
    # The cat surcharge table becomes part of the Knowledge Base so chat answers and the photo
    # estimate quote the same numbers (data/cat_pricing.json is the single source).
    cat_chunk = Chunk("pricing:cat", "ราคาอาบน้ำแมวตามขนาดและความยาวขน",
                      knowledge_text(cat_pricing, kb), "service_group", {"source": "cat_pricing.json"})
    chunks = build_chunks(kb, extra=[cat_chunk])
    note = " | หมายเหตุ: 500 บาทเป็นราคาตั้งต้นสำหรับแมวขนาดเล็กขนสั้น (ให้บริการเฉพาะแมวที่ยอมรับการดูแลได้อย่างปลอดภัย) แมวตัวใหญ่หรือขนยาวมีค่าบริการเพิ่ม (ดูราคาอาบน้ำแมวตามขนาดและความยาวขน)"
    for c in chunks:
        if c.id in {f"service:{cat_pricing.base_service_id}", "service_group:cat", "faq:02"}:
            c.text += note
        # Customers ask "มีโปรโมชั่นไหม"; the official policy is filed under "ส่วนลด".
        if c.id == "policy:ส่วนลด":
            c.text = "นโยบายส่วนลดและโปรโมชัน (โปรโมชั่น): " + c.text
    embedder = make_embedder(settings.embedding_backend, settings.embedding_model)
    store = VectorStore(embedder, settings.index_dir)
    store.build(chunks)
    state = AppState(
        settings=settings,
        kb=kb,
        rules=rules,
        store=store,
        retriever=Retriever(store, settings.top_k),
        llm=LLMClient(settings),
        system_prompt=build_system_prompt(rules),
        bookings=BookingStore(settings.bookings_db),
        cat_pricing=cat_pricing,
        allowed_extra_prices=price_numbers(cat_pricing, kb),
        startup_ms=(time.perf_counter() - t0) * 1000,
    )
    log.info(
        "PetCare Buddy ready: %d chunks, embedder=%s, llm=%s(configured=%s)",
        len(chunks), embedder.name, settings.llm_provider, settings.llm_configured,
    )
    return state


def get_state(request: Request) -> AppState:
    return request.app.state.petcare
