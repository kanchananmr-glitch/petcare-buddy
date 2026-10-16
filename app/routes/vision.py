"""POST /api/analyze-image - validated image upload + vision LLM grounded in the KB."""
from __future__ import annotations

import io
import json
import re
import time

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from app.llm import LLMError, LLMUnavailable
from app.models import ImageAnalysisResponse, ImageObservation, SafetyReport
from app.prompts import TRANSCRIBE_PROMPT, TRANSCRIBE_SYSTEM, build_vision_user_text
from app.routes.chat import add_missing_caveats, to_source_refs
from app.rag.retriever import RetrievedChunk
from app.safety.guardrails import SAFE_FALLBACK, check_input, guardrail_response, validate_output
from app.state import AppState, get_state
from app.vision_checks import injected_text_answer, instruction_flags, listed_prices, price_check_answer

router = APIRouter(prefix="/api", tags=["vision"])

ALLOWED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
MAX_EDGE = 1568  # downscale large photos before sending to the vision model
MIN_EDGE = 32
PINNED_CHUNKS = ["policy:รูปภาพ", "service_group:dog_bath", "service_group:dog_haircut", "service_group:cat"]
NUMBER_RE = re.compile(r"\d[\d,]*")
TEXT_IMAGE_TYPES = {"price_sign", "receipt", "notice"}


def validate_image(data: bytes, declared_type: str | None, max_mb: float) -> tuple[bytes, str, dict]:
    """Check size, real file format (magic bytes via Pillow) and dimensions."""
    if not data:
        raise HTTPException(400, detail={"code": "empty_file", "message": "ไม่พบไฟล์รูปภาพ"})
    if len(data) > max_mb * 1024 * 1024:
        raise HTTPException(
            413, detail={"code": "file_too_large", "message": f"ไฟล์มีขนาดเกิน {max_mb:g} MB กรุณาลดขนาดรูปภาพ"}
        )
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()  # detects truncated / corrupt files
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise HTTPException(
            415, detail={"code": "invalid_image", "message": "ไฟล์นี้ไม่ใช่รูปภาพที่อ่านได้ (รองรับ JPG, PNG, WEBP)"}
        ) from exc

    fmt = img.format or ""
    if fmt not in ALLOWED_FORMATS:
        raise HTTPException(
            415, detail={"code": "unsupported_format", "message": f"ไม่รองรับไฟล์ประเภท {fmt or 'ไม่ทราบ'} (รองรับ JPG, PNG, WEBP)"}
        )
    w, h = img.size
    if min(w, h) < MIN_EDGE:
        raise HTTPException(422, detail={"code": "image_too_small", "message": "รูปภาพมีขนาดเล็กเกินไปสำหรับการวิเคราะห์"})

    info = {
        "format": fmt,
        "declared_content_type": declared_type,
        "width": w,
        "height": h,
        "bytes": len(data),
        "resized": False,
    }
    media_type = ALLOWED_FORMATS[fmt]
    if max(w, h) > MAX_EDGE:
        img.thumbnail((MAX_EDGE, MAX_EDGE))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=88)
        data, media_type = buf.getvalue(), "image/jpeg"
        info.update(resized=True, sent_width=img.size[0], sent_height=img.size[1])
    return data, media_type, info


def parse_vision_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {"answer": text.strip()}


@router.post("/analyze-image", response_model=ImageAnalysisResponse)
async def analyze_image(
    file: UploadFile = File(..., description="รูปภาพ JPG/PNG/WEBP"),
    question: str = Form("", max_length=1000),
    state: AppState = Depends(get_state),
) -> ImageAnalysisResponse:
    t0 = time.perf_counter()
    max_bytes = int(state.settings.max_image_mb * 1024 * 1024)
    raw = await file.read(max_bytes + 1)
    data, media_type, info = validate_image(raw, file.content_type, state.settings.max_image_mb)
    question = question.strip()

    # Input guard on the typed question (text inside the image is handled by prompt + output guard)
    check = check_input(question) if question else None
    if check and check.blocked:
        answer, source_ids = guardrail_response(check.primary, state.kb, state.rules["unavailable_message"])
        results = [RetrievedChunk(c, 1.0) for sid in source_ids if (c := state.store.get(sid))]
        return ImageAnalysisResponse(
            answer=answer,
            mode="guardrail",
            observation=ImageObservation(),
            sources=to_source_refs(results, source_ids),
            safety=SafetyReport(input_flags=check.flags, blocked=True),
            image=info,
            timings_ms={"total": round((time.perf_counter() - t0) * 1000, 1)},
        )

    t1 = time.perf_counter()
    query = question or "บริการอาบน้ำ ตัดขน สุนัข แมว ราคา"
    results = state.retriever.retrieve(query, k=4, pinned=PINNED_CHUNKS)
    retrieval_ms = (time.perf_counter() - t1) * 1000

    t2 = time.perf_counter()
    try:
        llm_result = state.llm.generate(
            state.system_prompt, [], build_vision_user_text(question, results), image=data, media_type=media_type
        )
    except LLMUnavailable as exc:
        raise HTTPException(
            503,
            detail={
                "code": "vision_unavailable",
                "message": "ขออภัยค่ะ ขณะนี้ระบบวิเคราะห์รูปภาพยังไม่พร้อมใช้งาน จึงยังไม่ได้วิเคราะห์รูปนี้ "
                "กรุณาพิมพ์รายละเอียดสัตว์เลี้ยงแทน หรือติดต่อเจ้าหน้าที่ร้าน",
                "debug": str(exc),
            },
        ) from exc
    except LLMError as exc:
        raise HTTPException(
            502,
            detail={"code": "vision_error", "message": "ขออภัยค่ะ การวิเคราะห์รูปภาพขัดข้อง กรุณาลองใหม่อีกครั้ง", "debug": str(exc)},
        ) from exc
    llm_ms = (time.perf_counter() - t2) * 1000

    parsed = parse_vision_json(llm_result.text)
    observation = ImageObservation(
        image_type=str(parsed.get("image_type", "unknown")),
        observations=[str(o) for o in parsed.get("observations", []) if o][:8],
        visible_text=str(parsed.get("visible_text", ""))[:1000],
        contains_instructions=bool(parsed.get("contains_instructions", False)),
    )
    retrieved_ids = {r.chunk.id for r in results}
    cited = [s for s in parsed.get("sources", []) if s in retrieved_ids]

    # Numbers read from the image may be quoted back (e.g. "ป้ายระบุ 199 บาท") without being
    # treated as an unsupported price; claims of discounts/bookings are still blocked.
    visible_numbers = {int(n.replace(",", "")) for n in NUMBER_RE.findall(observation.visible_text) if n.replace(",", "").isdigit()}
    out = validate_output(str(parsed.get("answer", "")), state.kb, extra_allowed_numbers=visible_numbers | state.allowed_extra_prices)
    obs_check = validate_output(" ".join(observation.observations), state.kb, extra_allowed_numbers=visible_numbers | state.allowed_extra_prices)
    if obs_check.blocked:
        observation.observations = []

    flags = ["image_contains_instructions"] if observation.contains_instructions else []
    mode, answer = "rag", out.text
    if out.blocked or not answer:
        mode, answer = "fallback", SAFE_FALLBACK

    # Code-level checks on what the model READ (see app/vision_checks.py for why).
    corrections: list[str] = []
    if observation.image_type in TEXT_IMAGE_TYPES:
        # Re-read the text without the shop knowledge in view; the checks below use this copy.
        try:
            t3 = time.perf_counter()
            raw = state.llm.generate(TRANSCRIBE_SYSTEM, [], TRANSCRIBE_PROMPT, image=data, media_type=media_type)
            llm_ms += (time.perf_counter() - t3) * 1000
            copied = parse_vision_json(raw.text)
            text = str(copied.get("text") or "").strip()
            if text and not text.startswith("{"):
                observation.visible_text = text[:1000]
                corrections.append("text_reread")
        except (LLMUnavailable, LLMError):
            pass  # keep the first reading
    injected = instruction_flags(observation.visible_text, observation.observations)
    if injected:
        # The picture tries to instruct the bot: refuse from policy text, whatever the model wrote.
        observation.contains_instructions = True
        flags = ["image_contains_instructions"] + [f"image_text:{f}" for f in injected]
        answer = injected_text_answer(state.kb)
        cited = [cid for cid in ("policy:การจอง", "policy:ส่วนลด", "policy:รูปภาพ") if state.store.get(cid)]
        corrections.append("instructions_in_image")
    elif (lines := listed_prices(observation.visible_text, state.kb)) and (
        observation.image_type in ("price_sign", "receipt") or len(lines) >= 2
    ):
        # Prices on a sign or receipt: compare each against the KB by exact service name.
        answer = price_check_answer(lines, observation.visible_text, observation.image_type, state.kb)
        cited = [f"service:{ln.service.id}" for ln in lines]
        # Show what the picture says, not the model's own (possibly wrong) verdicts.
        observation.observations = [f"ในภาพระบุ {ln.service.name} {ln.listed} บาท" for ln in lines]
        corrections.append("kb_price_check")
    else:
        answer, caveat_ids = add_missing_caveats(answer, state)
        if caveat_ids:
            corrections.append("added_caveat")
            cited = list(dict.fromkeys(cited + caveat_ids))
    if corrections:
        mode = "guardrail" if "instructions_in_image" in corrections else "rag"
        have = {r.chunk.id for r in results}
        results = results + [RetrievedChunk(c, 0.0) for cid in cited if cid not in have and (c := state.store.get(cid))]

    return ImageAnalysisResponse(
        answer=answer,
        mode=mode,
        observation=observation,
        sources=to_source_refs(results, cited),
        safety=SafetyReport(
            input_flags=flags,
            output_violations=out.violations + [f"observation:{v}" for v in obs_check.violations] + corrections,
            blocked=out.blocked,
        ),
        image=info,
        provider=llm_result.provider,
        model=llm_result.model,
        timings_ms={
            "retrieval": round(retrieval_ms, 1),
            "llm": round(llm_ms, 1),
            "total": round((time.perf_counter() - t0) * 1000, 1),
        },
    )
