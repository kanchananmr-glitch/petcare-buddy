"""Booking request endpoints (customer) and confirmation endpoints (staff)."""
from __future__ import annotations

import json
import secrets
from collections import Counter
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from app.booking import (
    MAX_DAYS_AHEAD,
    STATUS_LABELS,
    SlotTakenError,
    appointment_at,
    booking_options,
    cancel_notice_hours,
    free_slots,
    group_ranges,
    mask_contact,
    store_tz,
    time_slots,
    validate_request,
)
from app.models import BookingCreate, ContactProof, StaffDecision
from app.state import AppState, get_state

router = APIRouter(prefix="/api", tags=["booking"])


def now_in_store(state: AppState) -> datetime:
    """Current time in the store's timezone. Tests replace this function."""
    return datetime.now(store_tz(state.settings.timezone))


def public_view(row: dict, state: AppState, include_contact: bool = False) -> dict:
    by_id = {s.id: s for s in state.kb.services}
    services = [
        {"id": sid, "name": by_id[sid].name, "price": by_id[sid].price}
        for sid in json.loads(row["service_ids"])
        if sid in by_id
    ]
    view = {
        "booking_id": row["id"],
        "status": row["status"],
        "status_label": STATUS_LABELS[row["status"]],
        "pet_type": row["pet_type"],
        "pet_name": row["pet_name"],
        "weight_kg": row["weight_kg"],
        "services": services,
        "estimated_total": row["estimated_total"],
        "date": row["appt_date"],
        "time": row["appt_time"],
        "contact_name": row["contact_name"],
        "contact": row["contact"] if include_contact else mask_contact(row["contact"]),
        "cat_size": row.get("cat_size"),
        "coat_length": row.get("coat_length"),
        "extra_items": json.loads(row["extra_items"]) if row.get("extra_items") else [],
        "note": row["note"],
        "staff_note": row["staff_note"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    return view


def can_cancel(row: dict, state: AppState) -> tuple[bool, str | None]:
    if row["status"] not in {"pending", "confirmed"}:
        return False, f"คำขอนี้อยู่ในสถานะ '{STATUS_LABELS[row['status']]}' จึงยกเลิกไม่ได้"
    hours = cancel_notice_hours(state.kb)
    tz = store_tz(state.settings.timezone)
    if appointment_at(row, tz) - now_in_store(state) < timedelta(hours=hours):
        return False, (
            f"ตามนโยบายของร้าน ต้องแจ้งยกเลิกล่วงหน้าอย่างน้อย {hours} ชั่วโมงก่อนเวลานัด "
            "เวลาที่เหลือน้อยกว่านี้แล้ว กรุณาติดต่อพนักงานร้านโดยตรงค่ะ"
        )
    return True, None


# ------------------------------------------------------------------ customer endpoints


@router.get("/booking-options")
def get_booking_options(state: AppState = Depends(get_state)):
    """Opening hours, cancellation notice and services, all read from the Knowledge Base."""
    return booking_options(state.kb, state.cat_pricing)


def availability(state: AppState, day: date, now: datetime) -> dict:
    """Free and taken hourly slots for one day - times only, never who booked them."""
    taken_counts = state.bookings.slot_counts(day)
    cap = state.settings.slot_capacity
    free = free_slots(state.kb, day, now, taken_counts, capacity=cap)
    return {
        "date": day.isoformat(),
        "slot_minutes": 60,
        "capacity": cap,
        "free": free,
        "taken": sorted(s for s, n in taken_counts.items() if n >= cap),
        "free_ranges": [{"from": a, "to": b} for a, b in group_ranges(free)],
    }


@router.get("/availability")
def get_availability(
    response: Response,
    day: date | None = Query(None, alias="date", description="วันที่ (ค่าเริ่มต้น: วันนี้)"),
    state: AppState = Depends(get_state),
):
    """Hourly slots still open for booking on one day. Used by the booking form and the chat."""
    response.headers["Cache-Control"] = "no-store"
    now = now_in_store(state)
    day = day or now.date()
    if day < now.date() or day > now.date() + timedelta(days=MAX_DAYS_AHEAD):
        raise HTTPException(
            422,
            detail={"code": "date_out_of_range", "message": f"ดูคิวว่างได้ตั้งแต่วันนี้ถึงอีก {MAX_DAYS_AHEAD} วันข้างหน้า"},
        )
    return availability(state, day, now)


def _invalid(errors: list[str], suggestions: list[dict] | None = None) -> HTTPException:
    return HTTPException(
        422,
        detail={
            "code": "booking_invalid",
            "message": "ยังส่งคำขอจองไม่ได้ กรุณาแก้ไขข้อมูลต่อไปนี้",
            "errors": errors,
            "suggestions": suggestions or [],
        },
    )


@router.post("/bookings", status_code=201)
def create_booking(req: BookingCreate, state: AppState = Depends(get_state)):
    now = now_in_store(state)
    cap = state.settings.slot_capacity
    check = validate_request(req, state.kb, now, state.cat_pricing, taken=state.bookings.slot_counts(req.date), capacity=cap)
    if not check.ok:
        by_id = {s.id: s for s in state.kb.services}
        raise _invalid(check.errors, [{"id": s, "name": by_id[s].name, "price": by_id[s].price} for s in check.suggestions])
    try:
        row = state.bookings.create(req, check, now, capacity=cap)
    except SlotTakenError as e:  # someone else took the slot after validation
        raise _invalid([str(e)])
    view = public_view(row, state)
    view["warnings"] = check.warnings
    view["message"] = (
        f"ส่งคำขอจองเรียบร้อยค่ะ หมายเลขคำขอ {row['id']} สถานะ: {STATUS_LABELS['pending']} "
        "คิวจะยืนยันเมื่อพนักงานตรวจสอบแล้ว ราคาเป็นราคาประมาณการ ยืนยันราคาสุดท้ายและชำระเงินที่ร้านหลังรับบริการ"
    )
    return view


@router.get("/bookings/{booking_id}")
def get_booking(
    booking_id: str,
    response: Response,
    contact: str = Query(..., min_length=3, max_length=60, description="เบอร์โทรหรือ LINE ID ที่ใช้ตอนจอง"),
    state: AppState = Depends(get_state),
):
    response.headers["Cache-Control"] = "no-store"  # status changes when staff confirm; never cache it
    row = state.bookings.get_for_customer(booking_id, contact)
    if row is None:  # same answer for "no such id" and "wrong contact"
        raise HTTPException(
            404, detail={"code": "booking_not_found", "message": "ไม่พบคำขอจองที่ตรงกับหมายเลขและช่องทางติดต่อนี้"}
        )
    view = public_view(row, state)
    allowed, reason = can_cancel(row, state)
    view["can_cancel"] = allowed
    view["cancel_blocked_reason"] = reason
    return view


@router.post("/bookings/{booking_id}/cancel")
def cancel_booking(booking_id: str, proof: ContactProof, state: AppState = Depends(get_state)):
    row = state.bookings.get_for_customer(booking_id, proof.contact)
    if row is None:
        raise HTTPException(
            404, detail={"code": "booking_not_found", "message": "ไม่พบคำขอจองที่ตรงกับหมายเลขและช่องทางติดต่อนี้"}
        )
    allowed, reason = can_cancel(row, state)
    if not allowed:
        raise HTTPException(409, detail={"code": "cancel_not_allowed", "message": reason})
    row = state.bookings.set_status(row["id"], "cancelled", now_in_store(state))
    view = public_view(row, state)
    view["message"] = f"ยกเลิกคำขอจอง {row['id']} แล้วค่ะ"
    return view


# ------------------------------------------------------------------ staff endpoints


def require_staff(
    state: AppState = Depends(get_state),
    x_admin_token: str | None = Header(None, alias="X-Admin-Token"),
) -> AppState:
    expected = state.settings.admin_token
    if not expected:
        raise HTTPException(
            503, detail={"code": "admin_disabled", "message": "ยังไม่ได้ตั้งค่า ADMIN_TOKEN ในไฟล์ .env"}
        )
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(401, detail={"code": "unauthorized", "message": "รหัสพนักงานไม่ถูกต้อง"})
    return state


@router.get("/admin/bookings")
def staff_list(status: str | None = Query(None, pattern="^(pending|confirmed|rejected|cancelled)$"),
               state: AppState = Depends(require_staff)):
    rows = state.bookings.list(status)
    return {"count": len(rows), "bookings": [public_view(r, state, include_contact=True) for r in rows]}


@router.post("/admin/bookings/{booking_id}/status")
def staff_decide(booking_id: str, decision: StaffDecision, state: AppState = Depends(require_staff)):
    row = state.bookings.get(booking_id)
    if row is None:
        raise HTTPException(404, detail={"code": "booking_not_found", "message": "ไม่พบคำขอจองนี้"})
    if row["status"] == "cancelled":
        raise HTTPException(409, detail={"code": "already_cancelled", "message": "ลูกค้ายกเลิกคำขอนี้แล้ว"})
    try:
        row = state.bookings.set_status(
            row["id"], decision.status, now_in_store(state), decision.staff_note, capacity=state.settings.slot_capacity
        )
    except SlotTakenError as e:  # e.g. re-confirming a rejected request after someone else booked that hour
        raise HTTPException(409, detail={"code": "slot_taken", "message": str(e)})
    return public_view(row, state, include_contact=True)


# ------------------------------------------------------------------ staff dashboard

ACTIVE = ("pending", "confirmed")  # requests that still hold a slot


@router.get("/admin/dashboard")
def staff_dashboard(
    response: Response,
    start: date | None = Query(None, description="วันแรกของช่วง (ค่าเริ่มต้น: วันนี้)"),
    days: int = Query(7, ge=1, le=62, description="จำนวนวันที่แสดง"),
    state: AppState = Depends(require_staff),
):
    """Overview + per-day booking ids + every booking in the range, all read from the bookings database."""
    response.headers["Cache-Control"] = "no-store"
    today = now_in_store(state).date()
    start = start or today
    end = start + timedelta(days=days - 1)
    rows = state.bookings.list_between(start, end)
    by_service_name = {s.id: s.name for s in state.kb.services}

    per_day: dict[str, list[dict]] = {(start + timedelta(days=i)).isoformat(): [] for i in range(days)}
    for r in rows:
        per_day[r["appt_date"]].append(r)

    def status_counts(items: list[dict]) -> dict[str, int]:
        c = Counter(r["status"] for r in items)
        return {s: c.get(s, 0) for s in STATUS_LABELS}

    service_counter: Counter = Counter()
    for r in rows:
        if r["status"] in ACTIVE:
            service_counter.update(json.loads(r["service_ids"]))

    active = [r for r in rows if r["status"] in ACTIVE]
    return {
        "today": today.isoformat(),
        "range": {"start": start.isoformat(), "end": end.isoformat(), "days": days},
        "status_labels": STATUS_LABELS,
        "time_slots": time_slots(state.kb),
        "all_time": state.bookings.status_totals(),
        "summary": {
            "total": len(rows),
            "by_status": status_counts(rows),
            "active": len(active),
            "estimated_revenue": sum(r["estimated_total"] for r in active),
            "busiest_day": max(per_day, key=lambda d: sum(r["status"] in ACTIVE for r in per_day[d])) if active else None,
        },
        "days": [
            {
                "date": d,
                "count": len(items),
                "active": sum(r["status"] in ACTIVE for r in items),
                "by_status": status_counts(items),
                "booking_ids": [r["id"] for r in items],
            }
            for d, items in per_day.items()
        ],
        "services": [
            {"id": sid, "name": by_service_name.get(sid, sid), "count": n}
            for sid, n in service_counter.most_common()
        ],
        "bookings": [public_view(r, state, include_contact=True) for r in rows],
    }


# ------------------------------------------------------------------ public queue board (storefront)

QUEUE_DAYS = 7  # the storefront shows today + the next 6 days


def _queue_item(r: dict, by_id: dict, queue_no: int) -> dict:
    """Only what a public queue board needs - never the booking number, name, contact, pet name,
    weight or notes. Booking numbers (PET-BOOK-DDMMYYYYnn) follow a predictable pattern, so the
    public board shows a queue number for the day instead; customers find their own request with
    the booking number + contact on the status page."""
    return {
        "queue_no": queue_no,
        "queue_label": f"คิวที่ {queue_no:02d}",
        "time": r["appt_time"],
        "status": r["status"],
        "status_label": STATUS_LABELS[r["status"]],
        "pet_type": r["pet_type"],
        "services": [{"id": sid, "name": by_id[sid].name} for sid in json.loads(r["service_ids"]) if sid in by_id],
    }


@router.get("/queue", tags=["booking"])
def queue_board(
    response: Response,
    day: date | None = Query(None, alias="date", description="วันที่ต้องการดู (วันนี้ถึงอีก 6 วัน, ค่าเริ่มต้น: วันนี้)"),
    state: AppState = Depends(get_state),
):
    """Queue for one day (today .. today+6) for the storefront page, plus per-day counts for the day picker."""
    response.headers["Cache-Control"] = "no-store"
    now = now_in_store(state)
    today = now.date()
    last = today + timedelta(days=QUEUE_DAYS - 1)
    day = day or today
    if not (today <= day <= last):
        raise HTTPException(
            422,
            detail={
                "code": "queue_date_out_of_range",
                "message": f"ดูคิวได้ตั้งแต่วันนี้ถึงอีก {QUEUE_DAYS - 1} วันข้างหน้าเท่านั้น ({today.isoformat()} ถึง {last.isoformat()})",
            },
        )
    by_id = {s.id: s for s in state.kb.services}
    rows = state.bookings.list_between(today, last)
    per_day = Counter(r["appt_date"] for r in rows if r["status"] in ACTIVE)
    selected = [r for r in rows if r["appt_date"] == day.isoformat()]
    counts = Counter(r["status"] for r in selected)
    return {
        "date": day.isoformat(),
        "today": today.isoformat(),
        "now": now.strftime("%H:%M"),
        "days": [
            {"date": (d := today + timedelta(days=i)).isoformat(), "active": per_day.get(d.isoformat(), 0)}
            for i in range(QUEUE_DAYS)
        ],
        "counts": {s: counts.get(s, 0) for s in STATUS_LABELS},
        "items": [_queue_item(r, by_id, n) for n, r in enumerate(selected, start=1)],
    }


@router.get("/queue/today", tags=["booking"])
def queue_today(response: Response, state: AppState = Depends(get_state)):
    """Same as /api/queue for today (kept for older pages)."""
    return queue_board(response, None, state)
