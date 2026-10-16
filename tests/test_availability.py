"""Free-slot answers in the chat (วันนี้ / พรุ่งนี้) and the "คิวที่คุณเลือกถูกจองแล้ว" check."""
from __future__ import annotations

from datetime import date, datetime, time

import pytest

import app.routes.booking as booking_routes
import app.routes.chat as chat_routes
from app.booking import SLOT_TAKEN_MESSAGE, SlotTakenError, free_slots, group_ranges, slot_key, store_tz
from app.config import get_settings
from app.models import BookingCreate
from app.rag.loader import load_knowledge_base

TZ = store_tz("Asia/Bangkok")
NOW = datetime(2026, 10, 13, 10, 0, tzinfo=TZ)  # Tuesday 10:00
KB = load_knowledge_base(get_settings().knowledge_path)


@pytest.fixture(autouse=True)
def _fresh(client, monkeypatch):
    with client.app.state.petcare.bookings._connect() as db:
        db.execute("DELETE FROM bookings")
    monkeypatch.setattr(booking_routes, "now_in_store", lambda state: NOW)
    monkeypatch.setattr(chat_routes, "now_in_store", lambda state: NOW)
    yield


def book(client, day="2026-10-14", at="10:00", contact="0810000000", **extra):
    body = {
        "pet_type": "สุนัข", "pet_name": "โบ้", "weight_kg": 12, "service_ids": ["S02"],
        "date": day, "time": at, "contact_name": "ลูกค้า", "contact": contact, "note": None,
    }
    body.update(extra)
    return client.post("/api/bookings", json=body)


# ------------------------------------------------------------------ helpers


def test_free_slots_skip_taken_and_past_times():
    today = free_slots(KB, NOW.date(), NOW, {"11:00": 1})
    assert today == ["12:00", "13:00", "14:00", "15:00", "16:00", "17:00", "18:00"]  # 10:00 is now, 11:00 taken
    assert free_slots(KB, date(2026, 10, 14), NOW, {}, capacity=1)[0] == "09:00"
    assert "11:00" in free_slots(KB, NOW.date(), NOW, {"11:00": 1}, capacity=2)


def test_group_ranges():
    assert group_ranges(["09:00", "10:00", "11:00", "13:00", "15:00", "16:00"]) == [
        ("09:00", "11:00"), ("13:00", "13:00"), ("15:00", "16:00")
    ]
    assert group_ranges([]) == []


def test_slot_key_maps_old_half_hour_times_to_their_hour():
    assert slot_key("10:00") == "10:00" and slot_key("14:30") == "14:00" and slot_key(time(9, 59)) == "09:00"


def test_old_half_hour_booking_blocks_its_hour(client):
    """Bookings made before the switch to hourly slots (e.g. 14:30) still hold 14:00."""
    state = client.app.state.petcare
    stamp = NOW.isoformat()
    with state.bookings._connect() as db:
        db.execute(
            "INSERT INTO bookings (id, status, pet_type, service_ids, estimated_total, appt_date, appt_time,"
            " contact_name, contact, contact_key, created_at, updated_at)"
            " VALUES ('BK-OLD143', 'pending', 'สุนัข', '[\"S01\"]', 350, '2026-10-14', '14:30', 'x',"
            " '0800000000', '0800000000', ?, ?)",
            (stamp, stamp),
        )
    d = client.get("/api/availability", params={"date": "2026-10-14"}).json()
    assert d["taken"] == ["14:00"] and "14:00" not in d["free"]
    r = book(client, at="14:00")
    assert r.status_code == 422 and r.json()["detail"]["errors"][0].startswith(SLOT_TAKEN_MESSAGE)


# ------------------------------------------------------------------ booking form validation


def test_same_slot_cannot_be_booked_twice(client):
    assert book(client, contact="0811111111").status_code == 201
    r = book(client, contact="0822222222")
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["code"] == "booking_invalid"
    assert d["errors"][0].startswith(SLOT_TAKEN_MESSAGE)
    assert "10:00" in d["errors"][0]
    # another time on the same day is fine
    assert book(client, at="11:00", contact="0822222222").status_code == 201


def test_cancelled_or_rejected_booking_frees_the_slot(client):
    a = book(client, contact="0811111111").json()
    client.post(f"/api/bookings/{a['booking_id']}/cancel", json={"contact": "0811111111"})
    b = book(client, contact="0822222222")
    assert b.status_code == 201
    client.post(f"/api/admin/bookings/{b.json()['booking_id']}/status",
                headers={"X-Admin-Token": "staff-test-token"}, json={"status": "rejected"})
    assert book(client, contact="0833333333").status_code == 201


def test_staff_cannot_reconfirm_a_rejected_request_whose_slot_was_taken(client):
    staff = {"X-Admin-Token": "staff-test-token"}
    a = book(client, contact="0811111111").json()["booking_id"]
    client.post(f"/api/admin/bookings/{a}/status", headers=staff, json={"status": "rejected"})
    assert book(client, contact="0822222222").status_code == 201
    r = client.post(f"/api/admin/bookings/{a}/status", headers=staff, json={"status": "confirmed"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "slot_taken"


def test_store_rechecks_slot_inside_the_lock(client):
    """Two requests validated at the same moment: the second insert is refused."""
    state = client.app.state.petcare
    req = BookingCreate(pet_type="สุนัข", weight_kg=12, service_ids=["S02"], date=date(2026, 10, 14),
                        time=time(15, 0), contact_name="x", contact="0800000000")
    from app.booking import validate_request

    check = validate_request(req, state.kb, NOW, state.cat_pricing, taken={}, capacity=1)
    state.bookings.create(req, check, NOW, capacity=1)
    with pytest.raises(SlotTakenError, match=SLOT_TAKEN_MESSAGE):
        state.bookings.create(req, check, NOW, capacity=1)


def test_availability_endpoint_hides_customer_data(client):
    book(client, at="11:00", contact="0849999999", pet_name="ลับมาก")
    r = client.get("/api/availability", params={"date": "2026-10-14"})
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    d = r.json()
    assert d["taken"] == ["11:00"] and "11:00" not in d["free"] and d["free"][0] == "09:00"
    assert d["free_ranges"][:2] == [{"from": "09:00", "to": "10:00"}, {"from": "12:00", "to": "18:00"}]
    assert "0849999999" not in r.text and "ลับมาก" not in r.text
    assert client.get("/api/availability").json()["date"] == "2026-10-13"
    assert client.get("/api/availability", params={"date": "2026-10-12"}).status_code == 422


# ------------------------------------------------------------------ chat


@pytest.mark.parametrize("message, labels", [
    ("วันนี้มีคิวว่างไหม", ["วันนี้"]),
    ("พรุ่งนี้มีคิวว่างไหมคะ", ["พรุ่งนี้"]),
    ("Today available?", ["วันนี้"]),
    ("today+1 มีคิวไหม", ["พรุ่งนี้"]),
    ("วันนี้หรือพรุ่งนี้ว่างช่วงไหนบ้าง", ["วันนี้", "พรุ่งนี้"]),
])
def test_chat_answers_free_slots_from_database(client, fake_llm, message, labels):
    fake = fake_llm("ไม่ควรถูกเรียก")
    book(client, day="2026-10-13", at="12:00", contact="0811111111")
    book(client, day="2026-10-14", at="09:00", contact="0822222222")
    r = client.post("/api/chat", json={"message": message})
    assert r.status_code == 200
    d = r.json()
    assert d["mode"] == "availability" and d["actions"] == ["open_booking_form"]
    assert not fake.calls  # answered from the database, not the LLM
    for label in labels:
        assert f"📅 {label}" in d["answer"]
    if "วันนี้" in labels:
        assert "11:00–11:00" not in d["answer"] and "11:00 น., 13:00–18:00 น." in d["answer"]
        assert "เวลาที่ถูกจองแล้ว: 12:00 น." in d["answer"]
    if "พรุ่งนี้" in labels:
        assert "10:00–18:00 น." in d["answer"] and "ทุก 1 ชั่วโมง" in d["answer"]


def test_chat_full_day_and_closed_store(client, fake_llm, monkeypatch):
    fake_llm("ไม่ควรถูกเรียก")
    state = client.app.state.petcare
    stamp = NOW.isoformat()
    with state.bookings._connect() as db:
        for t in [f"{h:02d}:{m:02d}" for h in range(9, 19) for m in (0, 30) if (h, m) <= (18, 0)]:
            db.execute(
                "INSERT INTO bookings (id, status, pet_type, service_ids, estimated_total, appt_date, appt_time,"
                " contact_name, contact, contact_key, created_at, updated_at)"
                " VALUES (?, 'confirmed', 'สุนัข', '[\"S01\"]', 350, '2026-10-14', ?, 'x', '0800000000', '0800000000', ?, ?)",
                (f"T-{t}", t, stamp, stamp),
            )
    d = client.post("/api/chat", json={"message": "พรุ่งนี้มีคิวว่างไหม"}).json()
    assert "คิวเต็มแล้ว" in d["answer"]

    late = datetime(2026, 10, 13, 18, 30, tzinfo=TZ)
    monkeypatch.setattr(chat_routes, "now_in_store", lambda state: late)
    d = client.post("/api/chat", json={"message": "วันนี้ยังมีคิวว่างไหม"}).json()
    assert "ปิดรับคิวแล้ว" in d["answer"]


def test_chat_follow_up_uses_previous_question(client, fake_llm):
    fake_llm("ไม่ควรถูกเรียก")
    history = [{"role": "user", "content": "วันนี้มีคิวว่างไหม"}, {"role": "assistant", "content": "..."}]
    d = client.post("/api/chat", json={"message": "แล้วพรุ่งนี้ล่ะ", "history": history}).json()
    assert d["mode"] == "availability" and "📅 พรุ่งนี้" in d["answer"]


@pytest.mark.parametrize("message", ["วันนี้ร้านเปิดกี่โมง", "อาบน้ำแมวราคาเท่าไหร่", "เช็คสถานะ BK-ABC234 วันนี้ว่างไหม"])
def test_other_questions_still_go_to_rag(client, fake_llm, message):
    fake = fake_llm("ตอบจากข้อมูลร้านค่ะ\nSOURCES: faq:01")
    d = client.post("/api/chat", json={"message": message}).json()
    assert d["mode"] == "rag" and len(fake.calls) == 1
