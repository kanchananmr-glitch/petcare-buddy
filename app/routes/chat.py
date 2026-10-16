"""POST /api/chat - RAG chat endpoint with input and output guardrails."""
from __future__ import annotations

import re
import time

from fastapi import APIRouter, Depends, HTTPException

from datetime import date, datetime, timedelta

from app.booking import BOOKING_ID_PATTERN, free_slots, group_ranges, opening_hours, time_slots
from app.llm import LLMError, LLMUnavailable
from app.models import ChatRequest, ChatResponse, SafetyReport, SourceRef
from app.prompts import build_user_turn
from app.rag.retriever import RetrievedChunk
from app.safety.guardrails import SAFE_FALLBACK, check_input, guardrail_response, validate_output
from app.state import AppState, get_state
from app.routes.booking import now_in_store  # module-level name so tests can replace it

router = APIRouter(prefix="/api", tags=["chat"])

# Models do not always put the citation on its own line ("... ค่ะ 😊 SOURCES: faq:01"),
# and sometimes wrap it in markdown ("**SOURCES:**"), so match it anywhere up to end of line.
SOURCES_LINE = re.compile(r"[ \t]*[\*_]*SOURCES[\*_]*\s*:[\*_]*[ \t]*([^\n]*)", re.IGNORECASE)


def split_sources(text: str) -> tuple[str, list[str]]:
    """Remove the 'SOURCES: a,b' citation and return (answer, cited ids)."""
    cited: list[str] = []
    for m in SOURCES_LINE.finditer(text):
        cited += [s for s in re.split(r"[,\s]+", m.group(1).strip(" .*_")) if s and s.lower() != "none"]
    return SOURCES_LINE.sub("", text).strip(), cited


def to_source_refs(results: list[RetrievedChunk], cited: list[str]) -> list[SourceRef]:
    return [
        SourceRef(
            id=r.chunk.id,
            title=r.chunk.title,
            score=round(r.score, 4),
            excerpt=r.chunk.text[:160],
            cited=r.chunk.id in cited,
        )
        for r in results
    ]


def sanitize_history(state: AppState, history) -> list[dict]:
    """Keep the last N turns and drop turns that the input guard would block."""
    turns = [{"role": t.role, "content": t.content[:2000]} for t in history]
    clean: list[dict] = []
    skip_next_assistant = False
    for t in turns:
        if t["role"] == "user":
            skip_next_assistant = check_input(t["content"]).blocked
            if not skip_next_assistant:
                clean.append(t)
        elif not skip_next_assistant:
            clean.append(t)
    clean = clean[-state.settings.max_history_turns * 2 :]
    while clean and clean[0]["role"] != "user":  # providers require a user turn first
        clean.pop(0)
    # merge consecutive same-role turns (can happen after filtering)
    merged: list[dict] = []
    for t in clean:
        if merged and merged[-1]["role"] == t["role"]:
            merged[-1]["content"] += "\n" + t["content"]
        else:
            merged.append(dict(t))
    if merged and merged[-1]["role"] == "user":  # the new message is appended as the next user turn
        merged.pop()
    return merged


BOOKING_STATUS_RE = re.compile(
    BOOKING_ID_PATTERN + r"|(จอง|นัด|คิว).{0,20}(สถานะ|ยืนยันหรือยัง|ยืนยันแล้วหรือ|เช็ค|เช็ก|ตรวจ|ยกเลิก)"
    r"|(ยกเลิก|เช็ค|เช็ก|ตรวจ).{0,10}(การจอง|คิว|นัด)",
    re.IGNORECASE,
)
BOOKING_NEW_RE = re.compile(r"จอง|นัดหมาย|ขอนัด|ขอคิว|\bbook", re.IGNORECASE)


def booking_actions(message: str) -> list[str]:
    """Offer the booking form or the status form when the customer talks about bookings."""
    if BOOKING_STATUS_RE.search(message):
        return ["open_booking_status"]
    if BOOKING_NEW_RE.search(message):
        return ["open_booking_form"]
    return []

# ------------------------------------------------------------------ free-slot questions

# "วันนี้มีคิวว่างไหม", "พรุ่งนี้ว่างช่วงไหน", "Today available?", "today+1 มีคิวไหม"
AVAILABILITY_RE = re.compile(r"ว่าง|มีคิว|คิวเหลือ|available|free\s*slots?|\bopen\s*slots?", re.IGNORECASE)
TODAY_RE = re.compile(r"วันนี้|today(?!\s*\+)", re.IGNORECASE)
TOMORROW_RE = re.compile(r"พรุ่งนี้|today\s*\+\s*1|tomorrow", re.IGNORECASE)
THAI_DAYS = ["จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส.", "อา."]
THAI_MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def _days_in(text: str) -> list[str]:
    return [label for label, pat in (("วันนี้", TODAY_RE), ("พรุ่งนี้", TOMORROW_RE)) if pat.search(text)]


def availability_days(message: str, history) -> list[str]:
    """Which days ("วันนี้", "พรุ่งนี้") a free-slot question asks about; [] if it is not one.
    Booking-status questions ("เช็คสถานะ BK-... วันนี้ว่างไหม") are left to the normal flow."""
    if BOOKING_STATUS_RE.search(message):
        return []
    days = _days_in(message)
    if not days:
        return []
    if AVAILABILITY_RE.search(message):
        return days
    # short follow-up such as "แล้วพรุ่งนี้ล่ะ" right after a free-slot question
    prev = next((t.content for t in reversed(history) if t.role == "user"), "")
    if len(message) < 25 and prev and AVAILABILITY_RE.search(prev) and _days_in(prev):
        return days
    return []


def _fmt_ranges(slots: list[str]) -> str:
    return ", ".join(f"{a} น." if a == b else f"{a}–{b} น." for a, b in group_ranges(slots))


def availability_answer(state: AppState, labels: list[str], now: datetime) -> str:
    """Free hourly slots read from the bookings database (no LLM, no customer data)."""
    open_t, close_t = opening_hours(state.kb)
    cap = state.settings.slot_capacity
    lines = [f"คิวว่างสำหรับส่งคำขอจองค่ะ (เริ่มนัดได้ทุก 1 ชั่วโมง ช่วง {time_slots(state.kb)[0]}–{time_slots(state.kb)[-1]} น.)"]
    for label in labels:
        day: date = now.date() + timedelta(days=0 if label == "วันนี้" else 1)
        heading = f"📅 {label} ({THAI_DAYS[day.weekday()]} {day.day} {THAI_MONTHS[day.month - 1]})"
        counts = state.bookings.slot_counts(day)
        taken = sorted(s for s, n in counts.items() if n >= cap)
        free = free_slots(state.kb, day, now, counts, capacity=cap)
        if free:
            lines.append(f"{heading}: {_fmt_ranges(free)}")
        elif not free_slots(state.kb, day, now, {}, capacity=cap):
            lines.append(f"{heading}: ปิดรับคิวแล้ว (ร้านเปิด {open_t:%H:%M}–{close_t:%H:%M} น.)")
        else:
            lines.append(f"{heading}: คิวเต็มแล้ว")
        if taken:
            lines.append(f"   เวลาที่ถูกจองแล้ว: {', '.join(t + ' น.' for t in taken)}")
    lines.append('กดปุ่ม "จองคิว" เพื่อส่งคำขอจอง คิวจะยืนยันเมื่อพนักงานตรวจสอบแล้วค่ะ')
    return "\n".join(lines)



# Topics whose official answer is a single policy/FAQ entry. Their chunks are always placed in
# the context (the embedder may rank them below top-k for paraphrases such as "แอปธนาคาร"), and
# if the model still answers "ยังไม่มีข้อมูล" the answer is rebuilt from those KB entries.
TOPIC_RULES: list[tuple[str, re.Pattern, list[str]]] = [
    (
        "payment",
        re.compile(r"จ่าย|ชำระ|โอน(เงิน)?|แอป\s*ธนาคาร|mobile\s*banking|พร้อมเพย์|promptpay|qr|บัตรเครดิต|มัดจำ|\bpay", re.IGNORECASE),
        ["policy:การชำระเงิน", "faq:05"],
    ),
    (
        "promotion",
        re.compile(r"โปร(โมชั่น|โมชัน)?|ส่วนลด|ลดราคา|promotion|\bpromo|discount", re.IGNORECASE),
        ["policy:ส่วนลด", "faq:04"],
    ),
    ("cat", re.compile(r"แมว|\bcats?\b", re.IGNORECASE), ["service:S04", "pricing:cat"]),
]

# Service details that are eligibility/safety caveats, not just weight ranges.
CAVEAT_RE = re.compile(r"เฉพาะ|ไม่ใช่|ปลอดภัย")
UNAVAILABLE_MARKER = "ยังไม่มีข้อมูลดังกล่าว"


def matched_topics(message: str) -> list[tuple[str, list[str]]]:
    return [(name, ids) for name, pat, ids in TOPIC_RULES if pat.search(message)]


def kb_topic_answer(topic: str, state: AppState) -> tuple[str, list[str]] | None:
    """Answer built only from official KB text, used when the model wrongly says 'no information'."""
    kb = state.kb
    policy = {p.topic: p.text for p in kb.policies}
    if topic == "payment" and policy.get("การชำระเงิน"):
        return (
            f"ร้านรับชำระเงินที่ร้านหลังรับบริการค่ะ ({policy['การชำระเงิน']}) "
            "จึงยังไม่สามารถชำระล่วงหน้าผ่านแอปธนาคาร โอนเงิน หรือช่องทางออนไลน์ได้ค่ะ "
            "หากมีข้อสงสัยเพิ่มเติมติดต่อพนักงานร้านได้เลยนะคะ",
            ["policy:การชำระเงิน", "faq:05"],
        )
    if topic == "promotion" and policy.get("ส่วนลด"):
        return (
            f"ขณะนี้ไม่มีโปรโมชันค่ะ ({policy['ส่วนลด']}) "
            "ราคาบริการเป็นไปตามรายการราคาของร้าน หากมีโปรโมชันใหม่ พนักงานร้านจะแจ้งให้ทราบค่ะ",
            ["policy:ส่วนลด", "faq:04"],
        )
    return None


def add_missing_caveats(answer: str, state: AppState) -> tuple[str, list[str]]:
    """If the answer quotes a service but drops its official condition, append the condition."""
    added: list[str] = []
    notes: list[str] = []
    for s in state.kb.services:
        if not s.detail or not CAVEAT_RE.search(s.detail) or s.name not in answer:
            continue
        if s.detail in answer:
            continue
        key_words = [w for w in ("ปลอดภัย", "ไม่ใช่การรักษา", "ไม่ใช่การขูดหินปูน", "ไม่ใช่บริการรักษา") if w in s.detail]
        if key_words and all(w in answer for w in key_words):
            continue
        notes.append(f"เงื่อนไขบริการ{s.name}: {s.detail}")
        added.append(f"service:{s.id}")
    if notes:
        answer = answer.rstrip() + "\n\n📌 " + "\n📌 ".join(notes)
    return answer, added


def retrieval_query(message: str, history: list[dict]) -> str:
    """Short follow-ups ("แล้วแมวล่ะ") are expanded with the previous user question."""
    if len(message) < 20:
        prev = next((t["content"] for t in reversed(history) if t["role"] == "user"), "")
        if prev:
            return f"{prev} {message}"
    return message


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, state: AppState = Depends(get_state)) -> ChatResponse:
    t0 = time.perf_counter()
    if len(req.message) > state.settings.max_message_chars:
        raise HTTPException(422, detail={"code": "message_too_long", "message": "ข้อความยาวเกินกำหนด"})

    # 1) Input guard -----------------------------------------------------------
    check = check_input(req.message)
    if check.blocked:
        answer, source_ids = guardrail_response(check.primary, state.kb, state.rules["unavailable_message"])
        results = [RetrievedChunk(c, 1.0) for sid in source_ids if (c := state.store.get(sid))]
        return ChatResponse(
            answer=answer,
            mode="guardrail",
            actions=[],
            sources=to_source_refs(results, source_ids),
            safety=SafetyReport(input_flags=check.flags, blocked=True),
            timings_ms={"total": round((time.perf_counter() - t0) * 1000, 1)},
        )

    # 1b) Free-slot questions are answered from the bookings database, not by the LLM -------
    if labels := availability_days(req.message, req.history):
        return ChatResponse(
            answer=availability_answer(state, labels, now_in_store(state)),
            mode="availability",
            actions=["open_booking_form"],
            sources=[],
            safety=SafetyReport(),
            timings_ms={"total": round((time.perf_counter() - t0) * 1000, 1)},
        )

    # 2) Retrieval ---------------------------------------------------------------
    history = sanitize_history(state, req.history)
    t1 = time.perf_counter()
    topics = matched_topics(req.message)
    pinned = [cid for _, ids in topics for cid in ids]
    # The question itself is always searched; a short follow-up only ADDS context from the
    # previous question (seen live: "ร้านเปิดกี่โมง" asked after a discount question).
    results = state.retriever.retrieve(req.message, pinned=pinned)
    expanded = retrieval_query(req.message, history)
    if expanded != req.message:
        seen = {r.chunk.id for r in results}
        extra = [r for r in state.retriever.retrieve(expanded) if r.chunk.id not in seen]
        results = results + extra[:3]
    retrieval_ms = (time.perf_counter() - t1) * 1000

    # 3) Generation ---------------------------------------------------------------
    t2 = time.perf_counter()
    try:
        llm_result = state.llm.generate(state.system_prompt, history, build_user_turn(req.message, results))
    except LLMUnavailable as exc:
        raise HTTPException(
            503,
            detail={
                "code": "llm_unavailable",
                "message": "ระบบตอบคำถามอัตโนมัติยังไม่พร้อมใช้งาน (ยังไม่ได้ตั้งค่า LLM API key) กรุณาติดต่อเจ้าหน้าที่ร้าน",
                "debug": str(exc),
            },
        ) from exc
    except LLMError as exc:
        raise HTTPException(
            502,
            detail={
                "code": "llm_error",
                "message": "ขออภัยค่ะ ระบบตอบคำถามขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้ง",
                "debug": str(exc),
            },
        ) from exc
    llm_ms = (time.perf_counter() - t2) * 1000

    # 4) Output guard ---------------------------------------------------------------
    answer, cited = split_sources(llm_result.text)
    retrieved_ids = {r.chunk.id for r in results}
    cited = [c for c in cited if c in retrieved_ids]  # ignore ids the model invented
    corrections: list[str] = []
    if UNAVAILABLE_MARKER in answer:
        for topic, _ in topics:
            if fixed := kb_topic_answer(topic, state):
                answer, cited = fixed
                corrections.append(f"kb_answer:{topic}")
                break
    answer, caveat_ids = add_missing_caveats(answer, state)
    if caveat_ids:
        cited = list(dict.fromkeys(cited + [c for c in caveat_ids if c in retrieved_ids]))
        corrections.append("added_caveat")
    out = validate_output(answer, state.kb, extra_allowed_numbers=state.allowed_extra_prices)
    mode = "rag"
    final = out.text
    if out.blocked:
        final, mode = SAFE_FALLBACK, "fallback"

    return ChatResponse(
        answer=final,
        mode=mode,
        actions=booking_actions(req.message),
        sources=to_source_refs(results, cited),
        safety=SafetyReport(
            input_flags=[],
            output_violations=out.violations + [f"redacted:{r}" for r in out.redactions] + corrections,
            blocked=out.blocked,
        ),
        provider=llm_result.provider,
        model=llm_result.model,
        timings_ms={
            "retrieval": round(retrieval_ms, 1),
            "llm": round(llm_ms, 1),
            "total": round((time.perf_counter() - t0) * 1000, 1),
        },
    )
