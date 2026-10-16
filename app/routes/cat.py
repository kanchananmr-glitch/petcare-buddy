"""Cat photo -> size / weight / coat length -> estimated price.

POST /api/cat-estimate  (multipart: file)   vision LLM describes the cat, code computes the price
GET  /api/cat-pricing                        the pricing table (for choosing size/coat by hand)
"""
from __future__ import annotations

import time

from fastapi.concurrency import run_in_threadpool
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from app.cat_estimate import (
    CAT_SYSTEM_PROMPT,
    CAT_USER_PROMPT,
    COATS,
    SIZES,
    estimate_price,
    normalize_estimate,
    parse_cat_json,
    summary_message,
)
from app.llm import LLMError, LLMUnavailable
from app.routes.vision import validate_image
from app.safety.guardrails import validate_output
from app.state import AppState, get_state

router = APIRouter(prefix="/api", tags=["cat-estimate"])


@router.get("/cat-pricing")
def cat_pricing(
    size: str | None = Query(None, description="เล็ก | กลาง | ใหญ่ (ถ้าระบุพร้อม coat จะคำนวณราคาให้)"),
    coat: str | None = Query(None, description="ขนสั้น | ขนยาว"),
    state: AppState = Depends(get_state),
):
    table = state.cat_pricing.to_public(state.kb)
    if size or coat:
        if size not in SIZES or coat not in COATS:
            raise HTTPException(
                422, detail={"code": "invalid_cat_option", "message": "ขนาดต้องเป็น เล็ก/กลาง/ใหญ่ และขนต้องเป็น ขนสั้น/ขนยาว"}
            )
        table["estimate"] = estimate_price(state.cat_pricing, state.kb, size, coat)
    return table


@router.post("/cat-estimate")
async def cat_estimate(
    file: UploadFile = File(..., description="รูปแมว JPG/PNG/WEBP"),
    state: AppState = Depends(get_state),
):
    t0 = time.perf_counter()
    max_bytes = int(state.settings.max_image_mb * 1024 * 1024)
    raw = await file.read(max_bytes + 1)
    data, media_type, info = validate_image(raw, file.content_type, state.settings.max_image_mb)

    try:
        # the model call blocks for several seconds, so keep it off the event loop
        result = await run_in_threadpool(
            state.llm.generate, CAT_SYSTEM_PROMPT, [], CAT_USER_PROMPT, image=data, media_type=media_type
        )
    except LLMUnavailable as exc:
        raise HTTPException(
            503,
            detail={
                "code": "vision_unavailable",
                "message": "ระบบวิเคราะห์รูปภาพยังไม่พร้อมใช้งาน (ยังไม่ได้ตั้งค่า API key) "
                "กรุณาเลือกขนาดและลักษณะขนของแมวเองในแบบฟอร์มเพื่อดูราคาประมาณการ",
            },
        ) from exc
    except LLMError as exc:
        raise HTTPException(
            502, detail={"code": "vision_error", "message": "การวิเคราะห์รูปภาพขัดข้อง กรุณาลองใหม่อีกครั้ง"}
        ) from exc

    parsed = parse_cat_json(result.text)
    if not parsed:
        raise HTTPException(
            502,
            detail={"code": "vision_unreadable", "message": "อ่านผลการวิเคราะห์รูปไม่ได้ กรุณาลองรูปอื่นที่เห็นตัวแมวชัดเจน"},
        )
    est = normalize_estimate(parsed, state.cat_pricing)

    # Text written by the model is shown to customers, so it passes the same output guard as chat.
    if est["observations"] and validate_output(" ".join(est["observations"]), state.kb).blocked:
        est["observations"] = []

    timings = {"total": round((time.perf_counter() - t0) * 1000, 1)}
    base = {"image": info, "provider": result.provider, "model": result.model, "timings_ms": timings}
    if not est["is_cat"]:
        return {
            **base,
            **est,
            "price": None,
            "message": "ไม่พบแมวในรูปนี้ค่ะ กรุณาส่งรูปที่เห็นตัวน้องแมวชัดเจน (เห็นทั้งตัวจะประเมินได้แม่นขึ้น)",
        }

    price = estimate_price(state.cat_pricing, state.kb, est["size"], est["coat_length"])
    return {**base, **est, "price": price, "message": summary_message(est, price)}
