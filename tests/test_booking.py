"""Booking request tests: validation from the KB, privacy, cancellation policy, staff confirmation."""
from __future__ import annotations

import json
import re
from datetime import datetime

import pytest

import app.routes.booking as booking_routes
from app.booking import cancel_notice_hours, opening_hours, store_tz, weight_range
from app.config import get_settings
from app.rag.loader import load_knowledge_base

TZ = store_tz("Asia/Bangkok")  # works even without tzdata (Windows)
NOW = datetime(2026, 10, 13, 10, 0, tzinfo=TZ)
STAFF = {"X-Admin-Token": "staff-test-token"}
KB = load_knowledge_base(get_settings().knowledge_path)


@pytest.fixture(autouse=True)
def _empty_bookings(client):
    """One hourly slot holds one active request, so every test starts with an empty table."""
    with client.app.state.petcare.bookings._connect() as db:
        db.execute("DELETE FROM bookings")
    yield


@pytest.fixture()
def frozen(monkeypatch):
    clock = {"now": NOW}
    monkeypatch.setattr(booking_routes, "now_in_store", lambda state: clock["now"])
    return clock


def request(**overrides):
    body = {
        "pet_type": "สุนัข",
        "pet_name": "โบ้",
        "weight_kg": 12,
        "service_ids": ["S02", "S09"],
        "date": "2026-10-15",
        "time": "10:00",
        "contact_name": "ลูกค้าทดสอบ",
        "contact": "081-000-0000",
        "note": None,
    }
    body.update(overrides)
    return body


# ------------------------------------------------------------------ rules read from the KB


def test_rules_come_from_knowledge_base():
    assert [t.strftime("%H:%M") for t in opening_hours(KB)] == ["09:00", "18:00"]
    assert cancel_notice_hours(KB) == 3
    by_id = {s.id: s for s in KB.services}
    assert weight_range(by_id["S01"]) == (0.0, 5.0)
    assert weight_range(by_id["S02"]) == (5.0, 15.0)
    assert weight_range(by_id["S07"]) == (15.0, 30.0)
    assert weight_range(by_id["S16"]) == (0.0, 5.0)
    assert weight_range(by_id["S04"]) is None


def test_booking_options(client):
    body = client.get("/api/booking-options").json()
    assert body["open"] == "09:00" and body["close"] == "18:00" and body["cancel_notice_hours"] == 3
    assert len(body["services"]) == 16


# ------------------------------------------------------------------ create


def test_create_valid_request_is_pending(client, frozen):
    r = client.post("/api/bookings", json=request())
    assert r.status_code == 201, r.text
    body = r.json()
    assert re.fullmatch(r"PET-BOOK-15102026\d{2}", body["booking_id"]), body["booking_id"]  # appointment date 2026-10-15
    assert body["status"] == "pending" and body["status_label"] == "รอพนักงานยืนยัน"
    assert body["estimated_total"] == 450 + 100
    assert "รอพนักงานยืนยัน" in body["message"]
    assert "ยืนยันการจองเรียบร้อยแล้ว" not in body["message"]
    assert body["contact"] != "081-000-0000"  # masked
    assert any("S09" in w for w in body["warnings"])  # pet type for S09 not confirmed in the KB


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"weight_kg": 3}, "ไม่อยู่ในเงื่อนไขของ S02"),
        ({"weight_kg": None}, "ต้องระบุน้ำหนัก"),
        ({"weight_kg": 45, "service_ids": ["S03"]}, "ยังไม่มีบริการสำหรับน้ำหนักนี้"),
        ({"pet_type": "แมว", "service_ids": ["S02"]}, "สำหรับสุนัขเท่านั้น"),
        ({"service_ids": ["S04"]}, "สำหรับแมวเท่านั้น"),
        ({"service_ids": ["S99"]}, "ไม่พบบริการรหัส S99"),
        ({"service_ids": ["S09", "S09"]}, "เลือกบริการซ้ำกัน"),
        ({"time": "08:30"}, "เวลาเริ่มนัดต้องอยู่ระหว่าง 09:00–18:00 น. ทุก 1 ชั่วโมง"),
        ({"time": "08:59"}, "เวลาเริ่มนัดต้องอยู่ระหว่าง 09:00–18:00 น. ทุก 1 ชั่วโมง"),
        ({"time": "09:30"}, "เวลาเริ่มนัดต้องอยู่ระหว่าง 09:00–18:00 น. ทุก 1 ชั่วโมง"),
        ({"time": "12:15"}, "เวลาเริ่มนัดต้องอยู่ระหว่าง 09:00–18:00 น. ทุก 1 ชั่วโมง"),
        ({"time": "17:30"}, "เวลาเริ่มนัดต้องอยู่ระหว่าง 09:00–18:00 น. ทุก 1 ชั่วโมง"),
        ({"time": "18:01"}, "เวลาเริ่มนัดต้องอยู่ระหว่าง 09:00–18:00 น. ทุก 1 ชั่วโมง"),
        ({"time": "18:30"}, "เวลาเริ่มนัดต้องอยู่ระหว่าง 09:00–18:00 น. ทุก 1 ชั่วโมง"),
        ({"time": "19:00"}, "เวลาเริ่มนัดต้องอยู่ระหว่าง 09:00–18:00 น. ทุก 1 ชั่วโมง"),
        ({"time": "23:00"}, "เวลาเริ่มนัดต้องอยู่ระหว่าง 09:00–18:00 น. ทุก 1 ชั่วโมง"),
        ({"date": "2026-10-12"}, "ผ่านไปแล้ว"),
        ({"date": "2027-03-01"}, "ไม่เกิน 60 วัน"),
    ],
)
def test_invalid_requests_are_rejected_with_thai_reasons(client, frozen, overrides, expected):
    r = client.post("/api/bookings", json=request(**overrides))
    assert r.status_code == 422
    d = r.json()["detail"]
    assert d["code"] == "booking_invalid"
    assert any(expected in e for e in d["errors"]), d["errors"]


@pytest.mark.parametrize("t", ["09:00", "12:00", "17:00", "18:00", "10:00:00", "10:00+07:00"])
def test_hourly_times_inside_opening_hours_are_accepted(client, frozen, t):
    r = client.post("/api/bookings", json=request(time=t))
    assert r.status_code == 201, r.text
    assert r.json()["time"] == t[:5]


def test_booking_options_offer_only_slots_inside_opening_hours(client):
    slots = client.get("/api/booking-options").json()["time_slots"]
    # one start time per hour; the team decided the last queue of the day starts at 18:00
    assert slots == [f"{h:02d}:00" for h in range(9, 19)]
    assert client.get("/api/booking-options").json()["last_start"] == "18:00"


def test_store_timezone_falls_back_without_tzdata(monkeypatch):
    """On Windows without the tzdata package ZoneInfo() raises; booking must still work."""
    import zoneinfo

    from app.booking import store_tz

    def boom(name):
        raise zoneinfo.ZoneInfoNotFoundError(name)

    monkeypatch.setattr(zoneinfo, "ZoneInfo", boom)
    tz = store_tz("Asia/Bangkok")
    assert datetime(2026, 10, 15, 12, tzinfo=tz).utcoffset().total_seconds() == 7 * 3600


def test_create_booking_with_real_clock(client):
    """No frozen clock: exercises now_in_store() + timezone exactly like the running server."""
    from datetime import date, timedelta

    r = client.post("/api/bookings", json=request(date=(date.today() + timedelta(days=2)).isoformat()))
    assert r.status_code == 201, r.text


def test_cat_size_and_coat_add_surcharges(client, frozen):
    r = client.post("/api/bookings", json=request(
        pet_type="แมว", weight_kg=None, service_ids=["S04"], cat_size="ใหญ่", coat_length="ขนยาว"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["estimated_total"] == 500 + 200 + 150
    assert body["cat_size"] == "ใหญ่" and body["coat_length"] == "ขนยาว"
    assert [x["amount"] for x in body["extra_items"]] == [200, 150]


def test_cat_size_is_derived_from_weight(client, frozen):
    body = client.post("/api/bookings", json=request(
        pet_type="แมว", weight_kg=4.2, service_ids=["S04"], coat_length="ขนสั้น")).json()
    assert body["cat_size"] == "กลาง" and body["estimated_total"] == 600


def test_cat_details_rejected_for_dogs(client, frozen):
    r = client.post("/api/bookings", json=request(cat_size="เล็ก"))
    assert r.status_code == 422
    assert any("แมวเท่านั้น" in e for e in r.json()["detail"]["errors"])


def test_old_database_is_upgraded(tmp_path):
    import sqlite3

    from app.booking import BookingStore

    db = tmp_path / "old.db"
    with sqlite3.connect(db) as c:  # schema of the first release (no cat columns)
        c.execute("""CREATE TABLE bookings (id TEXT PRIMARY KEY, status TEXT NOT NULL, pet_type TEXT NOT NULL,
            pet_name TEXT, weight_kg REAL, service_ids TEXT NOT NULL, estimated_total INTEGER NOT NULL,
            appt_date TEXT NOT NULL, appt_time TEXT NOT NULL, contact_name TEXT NOT NULL, contact TEXT NOT NULL,
            contact_key TEXT NOT NULL, note TEXT, staff_note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    BookingStore(db)
    with sqlite3.connect(db) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(bookings)")}
    assert {"cat_size", "coat_length", "extra_items"} <= cols


def test_weight_mismatch_suggests_the_right_size(client, frozen):
    d = client.post("/api/bookings", json=request(weight_kg=3)).json()["detail"]
    assert [s["id"] for s in d["suggestions"]] == ["S01"]


def test_cat_booking_without_weight(client, frozen):
    r = client.post("/api/bookings", json=request(pet_type="แมว", weight_kg=None, service_ids=["S04"]))
    assert r.status_code == 201
    assert r.json()["estimated_total"] == 500


@pytest.mark.parametrize("field", ["contact", "contact_name"])
def test_contact_is_required(client, field):
    r = client.post("/api/bookings", json=request(**{field: "   "}))
    assert r.status_code == 422


# ------------------------------------------------------------------ status lookup (privacy)


def test_status_needs_matching_contact(client, frozen):
    bid = client.post("/api/bookings", json=request()).json()["booking_id"]
    ok = client.get(f"/api/bookings/{bid}", params={"contact": "0810000000"})  # dashes/spaces ignored
    assert ok.status_code == 200 and ok.json()["status"] == "pending"
    wrong = client.get(f"/api/bookings/{bid}", params={"contact": "0899999999"})
    missing = client.get("/api/bookings/PET-BOOK-0101202699", params={"contact": "0810000000"})
    assert wrong.status_code == missing.status_code == 404
    assert wrong.json() == missing.json()  # cannot tell whether the id exists


def test_no_public_listing(client):
    assert client.get("/api/bookings").status_code in (404, 405)


# ------------------------------------------------------------------ cancel (3-hour policy)


def test_cancel_allowed_more_than_3_hours_before(client, frozen):
    bid = client.post("/api/bookings", json=request()).json()["booking_id"]
    r = client.post(f"/api/bookings/{bid}/cancel", json={"contact": "081-000-0000"})
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    again = client.post(f"/api/bookings/{bid}/cancel", json={"contact": "081-000-0000"})
    assert again.status_code == 409


def test_cancel_blocked_within_3_hours(client, frozen):
    bid = client.post("/api/bookings", json=request(date="2026-10-13", time="12:00")).json()["booking_id"]
    status = client.get(f"/api/bookings/{bid}", params={"contact": "081-000-0000"}).json()
    assert status["can_cancel"] is False and "3 ชั่วโมง" in status["cancel_blocked_reason"]
    r = client.post(f"/api/bookings/{bid}/cancel", json={"contact": "081-000-0000"})
    assert r.status_code == 409 and "3 ชั่วโมง" in r.json()["detail"]["message"]


def test_cancel_with_wrong_contact(client, frozen):
    bid = client.post("/api/bookings", json=request()).json()["booking_id"]
    assert client.post(f"/api/bookings/{bid}/cancel", json={"contact": "0899999999"}).status_code == 404


# ------------------------------------------------------------------ staff


def test_staff_endpoints_need_token(client):
    assert client.get("/api/admin/bookings").status_code == 401
    assert client.get("/api/admin/bookings", headers={"X-Admin-Token": "wrong"}).status_code == 401


def test_staff_confirms_and_customer_sees_it(client, frozen):
    bid = client.post("/api/bookings", json=request()).json()["booking_id"]
    listed = client.get("/api/admin/bookings", params={"status": "pending"}, headers=STAFF).json()
    row = next(b for b in listed["bookings"] if b["booking_id"] == bid)
    assert row["contact"] == "081-000-0000"  # staff see the full contact

    r = client.post(f"/api/admin/bookings/{bid}/status", headers=STAFF,
                    json={"status": "confirmed", "staff_note": "รอรับน้องที่หน้าร้าน 10:30"})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"

    seen = client.get(f"/api/bookings/{bid}", params={"contact": "081-000-0000"}).json()
    assert seen["status_label"] == "พนักงานยืนยันคิวแล้ว"
    assert seen["staff_note"] == "รอรับน้องที่หน้าร้าน 10:30"


def test_staff_cannot_confirm_cancelled(client, frozen):
    bid = client.post("/api/bookings", json=request()).json()["booking_id"]
    client.post(f"/api/bookings/{bid}/cancel", json={"contact": "081-000-0000"})
    r = client.post(f"/api/admin/bookings/{bid}/status", headers=STAFF, json={"status": "confirmed"})
    assert r.status_code == 409


def test_admin_page_served(client):
    r = client.get("/admin")
    assert r.status_code == 200 and "คำขอจองคิว" in r.text


# ------------------------------------------------------------------ chat integration


@pytest.mark.parametrize(
    "message, action",
    [
        ("อยากจองคิวอาบน้ำพรุ่งนี้", "open_booking_form"),
        ("คิว BK-ABC234 ยืนยันหรือยัง", "open_booking_status"),
        ("คิว PET-BOOK-1710202602 ยืนยันหรือยัง", "open_booking_status"),
        ("ขอยกเลิกการจอง", "open_booking_status"),
    ],
)
def test_chat_offers_booking_shortcut(client, fake_llm, message, action):
    fake_llm("กดปุ่มจองคิวเพื่อส่งคำขอได้เลยค่ะ\nSOURCES: policy:การจอง")
    body = client.post("/api/chat", json={"message": message}).json()
    assert body["actions"] == [action]


def test_chat_without_booking_intent_has_no_action(client, fake_llm):
    fake_llm("ร้านเปิดทุกวัน 09:00–18:00 น. ค่ะ\nSOURCES: faq:01")
    assert client.post("/api/chat", json={"message": "ร้านเปิดกี่โมง"}).json()["actions"] == []


def test_llm_cannot_claim_a_confirmed_booking(client, fake_llm):
    fake_llm("ได้เลยค่ะ ยืนยันการจองเรียบร้อยแล้ว\nSOURCES: policy:การจอง")
    body = client.post("/api/chat", json={"message": "จองคิวพรุ่งนี้ 10 โมง"}).json()
    assert body["mode"] == "fallback"


def test_status_is_never_cached_and_each_id_returns_its_own_booking(client, frozen):
    a = client.post("/api/bookings", json=request(pet_name="มิว", contact="0811111111")).json()["booking_id"]
    b = client.post("/api/bookings", json=request(pet_name="ส้ม", contact="0822222222", time="15:00")).json()["booking_id"]
    ra = client.get(f"/api/bookings/{a}", params={"contact": "0811111111"})
    rb = client.get(f"/api/bookings/{b}", params={"contact": "0822222222"})
    assert ra.headers["cache-control"] == "no-store"
    assert (ra.json()["pet_name"], rb.json()["pet_name"]) == ("มิว", "ส้ม")
    assert rb.json()["time"] == "15:00"
    # B's number with A's contact must not reveal anything
    assert client.get(f"/api/bookings/{b}", params={"contact": "0811111111"}).status_code == 404


# ------------------------------------------------------------------ staff dashboard


def test_dashboard_needs_token(client):
    assert client.get("/api/admin/dashboard").status_code == 401


def test_dashboard_counts_come_from_the_database(client, frozen):
    a = client.post("/api/bookings", json=request(date="2026-10-14", time="09:00", contact="0831111111")).json()
    b = client.post("/api/bookings", json=request(date="2026-10-14", time="11:00", contact="0832222222")).json()
    c = client.post("/api/bookings", json=request(date="2026-10-16", time="13:00", contact="0833333333",
                                                  pet_type="แมว", weight_kg=None, service_ids=["S04"])).json()
    client.post(f"/api/admin/bookings/{b['booking_id']}/status", headers=STAFF, json={"status": "confirmed"})
    client.post(f"/api/bookings/{c['booking_id']}/cancel", json={"contact": "0833333333"})

    r = client.get("/api/admin/dashboard", params={"start": "2026-10-14", "days": 3}, headers=STAFF)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    d = r.json()
    assert d["today"] == "2026-10-13"
    assert d["range"] == {"start": "2026-10-14", "end": "2026-10-16", "days": 3}
    assert [x["date"] for x in d["days"]] == ["2026-10-14", "2026-10-15", "2026-10-16"]

    ours = {a["booking_id"], b["booking_id"], c["booking_id"]}
    day14 = d["days"][0]
    assert {a["booking_id"], b["booking_id"]} <= set(day14["booking_ids"])
    assert c["booking_id"] in d["days"][2]["booking_ids"]
    # each day's ids are exactly the bookings stored for that date
    for day in d["days"]:
        assert sorted(day["booking_ids"]) == sorted(x["booking_id"] for x in d["bookings"] if x["date"] == day["date"])
        assert day["count"] == len(day["booking_ids"]) == sum(day["by_status"].values())

    s = d["summary"]
    assert s["total"] == len(d["bookings"]) and ours <= {x["booking_id"] for x in d["bookings"]}
    assert s["by_status"]["cancelled"] >= 1 and s["by_status"]["confirmed"] >= 1
    active = [x for x in d["bookings"] if x["status"] in ("pending", "confirmed")]
    assert s["active"] == len(active)
    assert s["estimated_revenue"] == sum(x["estimated_total"] for x in active)
    assert d["time_slots"][0] == "09:00" and d["time_slots"][-1] == "18:00"
    assert any(x["contact"] == "0831111111" for x in d["bookings"])  # staff see full contact
    assert sum(d["all_time"].values()) >= s["total"]


def test_dashboard_defaults_to_today_and_validates_days(client, frozen):
    d = client.get("/api/admin/dashboard", headers=STAFF).json()
    assert d["range"]["start"] == "2026-10-13" and len(d["days"]) == 7
    assert client.get("/api/admin/dashboard", params={"days": 0}, headers=STAFF).status_code == 422
    assert client.get("/api/admin/dashboard", params={"days": 100}, headers=STAFF).status_code == 422


def test_dashboard_page_served(client):
    r = client.get("/dashboard")
    assert r.status_code == 200 and "Dashboard การจองคิว" in r.text


# ------------------------------------------------------------------ public queue board


def test_queue_today_is_public_and_hides_personal_data(client, frozen):
    body = request(date="2026-10-13", time="16:00", pet_name="ลับมาก", contact="0849999999",
                   contact_name="ชื่อลูกค้าลับ", note="หมายเหตุลับ")
    bid = client.post("/api/bookings", json=body).json()["booking_id"]
    other_day = client.post("/api/bookings", json=request(date="2026-10-14")).json()["booking_id"]

    r = client.get("/api/queue/today")  # no admin token
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    d = r.json()
    assert d["date"] == "2026-10-13" and d["now"] == "10:00"
    mine = next(x for x in d["items"] if x["time"] == "16:00")  # other tests share the database
    assert mine["status"] == "pending"
    assert [x["queue_no"] for x in d["items"]] == list(range(1, len(d["items"]) + 1))
    assert mine["queue_label"] == f"คิวที่ {mine['queue_no']:02d}"
    assert [s["id"] for s in mine["services"]] == ["S02", "S09"]
    assert set(mine) == {"queue_no", "queue_label", "time", "status", "status_label", "pet_type", "services"}
    # booking numbers follow a predictable pattern, so the public board never shows them
    secrets_ = ("ลับมาก", "0849999999", "ชื่อลูกค้าลับ", "หมายเหตุลับ", bid, other_day, "PET-BOOK")
    assert not any(secret in r.text for secret in secrets_)
    assert [x["time"] for x in d["items"]] == sorted(x["time"] for x in d["items"])
    assert sum(d["counts"].values()) == len(d["items"])


def test_queue_board_covers_today_to_six_days_ahead(client, frozen):
    a = client.post("/api/bookings", json=request(date="2026-10-19", time="11:00", contact="0851111111")).json()
    b = client.post("/api/bookings", json=request(date="2026-10-19", time="09:00", contact="0852222222")).json()
    client.post(f"/api/bookings/{b['booking_id']}/cancel", json={"contact": "0852222222"})

    d = client.get("/api/queue", params={"date": "2026-10-19"}).json()
    assert d["date"] == "2026-10-19" and d["today"] == "2026-10-13"
    assert [x["date"] for x in d["days"]] == [f"2026-10-{n}" for n in range(13, 20)]
    assert [(x["queue_no"], x["time"], x["status"]) for x in d["items"]] == [
        (1, "09:00", "cancelled"), (2, "11:00", "pending")]
    assert a["booking_id"] not in json.dumps(d) and b["booking_id"] not in json.dumps(d)
    day19 = next(x for x in d["days"] if x["date"] == "2026-10-19")
    assert day19["active"] == sum(x["status"] in ("pending", "confirmed") for x in d["items"])
    assert all(set(x) == {"queue_no", "queue_label", "time", "status", "status_label", "pet_type", "services"}
               for x in d["items"])

    assert client.get("/api/queue").json()["date"] == "2026-10-13"  # default = today
    for bad in ("2026-10-12", "2026-10-20", "not-a-date"):
        assert client.get("/api/queue", params={"date": bad}).status_code == 422


# ------------------------------------------------------------------ booking number format


def test_booking_numbers_run_per_appointment_date(tmp_path):
    from datetime import date

    from app.booking import BookingStore

    store = BookingStore(tmp_path / "ids.db")
    with store._connect() as db:
        assert store.new_id(db, date(2026, 10, 17)) == "PET-BOOK-1710202601"
        stamp = "2026-10-13T10:00:00+07:00"
        for bid, d in [("PET-BOOK-1710202601", "2026-10-17"), ("PET-BOOK-1710202602", "2026-10-17"),
                       ("BK-ABC234", "2026-10-17"), ("PET-BOOK-1810202601", "2026-10-18")]:
            db.execute(
                "INSERT INTO bookings (id, status, pet_type, service_ids, estimated_total, appt_date, appt_time,"
                " contact_name, contact, contact_key, created_at, updated_at)"
                " VALUES (?, 'pending', 'สุนัข', '[\"S01\"]', 350, ?, '10:00', 'x', '0800000000', '0800000000', ?, ?)",
                (bid, d, stamp, stamp),
            )
        assert store.new_id(db, date(2026, 10, 17)) == "PET-BOOK-1710202603"
        assert store.new_id(db, date(2026, 10, 18)) == "PET-BOOK-1810202602"
        assert store.new_id(db, date(2026, 11, 5)) == "PET-BOOK-0511202601"


def test_same_day_requests_get_consecutive_numbers(client, frozen):
    a = client.post("/api/bookings", json=request(date="2026-11-02", time="10:00", contact="0861111111")).json()
    b = client.post("/api/bookings", json=request(date="2026-11-02", time="11:00", contact="0862222222")).json()
    c = client.post("/api/bookings", json=request(date="2026-11-03", time="11:00", contact="0863333333")).json()
    na, nb = int(a["booking_id"][17:]), int(b["booking_id"][17:])
    assert a["booking_id"].startswith("PET-BOOK-02112026") and b["booking_id"].startswith("PET-BOOK-02112026")
    assert nb == na + 1
    assert c["booking_id"].startswith("PET-BOOK-03112026")
    # lookups are case-insensitive, like the old ids
    r = client.get(f"/api/bookings/{a['booking_id'].lower()}", params={"contact": "0861111111"})
    assert r.status_code == 200 and r.json()["booking_id"] == a["booking_id"]


def test_old_bk_numbers_still_work(client, frozen):
    state = client.app.state.petcare
    stamp = "2026-10-13T10:00:00+07:00"
    with state.bookings._connect() as db:
        db.execute(
            "INSERT INTO bookings (id, status, pet_type, service_ids, estimated_total, appt_date, appt_time,"
            " contact_name, contact, contact_key, created_at, updated_at)"
            " VALUES ('BK-OLD234', 'pending', 'สุนัข', '[\"S01\"]', 350, '2026-10-20', '10:00', 'x',"
            " '0877777777', '0877777777', ?, ?)",
            (stamp, stamp),
        )
    r = client.get("/api/bookings/BK-OLD234", params={"contact": "0877777777"})
    assert r.status_code == 200 and r.json()["booking_id"] == "BK-OLD234"
