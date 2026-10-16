"""Booking requests: validation against the Knowledge Base and local SQLite storage.

Store policy (data/knowledge.json):
  - การจอง: the chatbot may take booking requests, but a queue slot is only real
    after staff check it -> every request starts as "pending" and only staff
    (admin endpoints, protected by ADMIN_TOKEN) can set "confirmed" or "rejected".
  - การยกเลิก: cancel at least 3 hours before the appointment -> enforced on cancel.
  - การชำระเงิน: pay at the store -> no payment is taken here.
  - ข้อมูลส่วนบุคคล: never reveal another customer's booking -> a booking can only be
    read with its id AND the contact used to create it; there is no public list.

Slots: one start time per hour from opening to closing time (09:00-18:00, read from the KB).
A slot holds BOOKING_SLOT_CAPACITY active requests (pending or confirmed, default 1); a second
request for a full slot is refused, and the check is repeated inside the database lock so two
customers who press "send" at the same moment cannot both get the slot. Staff still confirm
every request.
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path

from app.cat_estimate import CatPricing, cat_surcharges
from app.models import KnowledgeBase, Service

STATUS_LABELS = {
    "pending": "รอพนักงานยืนยัน",
    "confirmed": "พนักงานยืนยันคิวแล้ว",
    "rejected": "ร้านไม่สามารถรับคิวนี้ได้",
    "cancelled": "ยกเลิกแล้ว",
}
MAX_DAYS_AHEAD = 60  # system limit for the prototype, not a store policy
SLOT_MINUTES = 60    # one bookable start time per hour
ACTIVE_STATUSES = ("pending", "confirmed")  # requests that hold a slot
SLOT_TAKEN_MESSAGE = "คิวที่คุณเลือกถูกจองแล้ว"
ID_PREFIX = "PET-BOOK-"  # new ids: PET-BOOK-<appointment date DDMMYYYY><running number for that date, 2 digits>
# Matches new ids and the older random ids (BK-XXXXXX) that are kept unchanged in existing databases.
BOOKING_ID_PATTERN = r"PET-BOOK-\d{10,}|BK-[A-Z0-9]{6}"


# ------------------------------------------------------------------ timezone


# Fixed offsets used when the OS has no timezone database (e.g. Windows without the
# `tzdata` package). Thailand has no daylight saving time, so a fixed +07:00 is exact.
_FALLBACK_OFFSETS = {"Asia/Bangkok": 7, "Asia/Ho_Chi_Minh": 7, "Asia/Jakarta": 7, "UTC": 0}


def store_tz(name: str) -> tzinfo:
    """ZoneInfo(name), falling back to a fixed offset instead of crashing with a 500 error."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # ZoneInfoNotFoundError, missing tzdata, bad name
        return timezone(timedelta(hours=_FALLBACK_OFFSETS.get(name, 7)), name)


# ------------------------------------------------------------------ rules derived from the KB


def opening_hours(kb: KnowledgeBase) -> tuple[time, time]:
    """Parse 'ทุกวัน 09:00–18:00 น.' -> (09:00, 18:00)."""
    m = re.search(r"(\d{1,2}):(\d{2})\s*[–\-]\s*(\d{1,2}):(\d{2})", kb.profile.hours)
    if not m:
        raise ValueError(f"cannot read opening hours from KB: {kb.profile.hours!r}")
    h1, m1, h2, m2 = map(int, m.groups())
    return time(h1, m1), time(h2, m2)


def cancel_notice_hours(kb: KnowledgeBase) -> int:
    """Read 'อย่างน้อย 3 ชั่วโมง' from the official cancellation policy."""
    text = next((p.text for p in kb.policies if p.topic == "การยกเลิก"), "")
    m = re.search(r"(\d+)\s*ชั่วโมง", text)
    if not m:
        raise ValueError("cannot read the cancellation notice period from the KB")
    return int(m.group(1))


def weight_range(service: Service) -> tuple[float, float] | None:
    """(min_exclusive, max_inclusive) in kg from the service detail, or None if not stated."""
    m = re.search(r"มากกว่า\s*(\d+(?:\.\d+)?)\s*ถึง\s*(\d+(?:\.\d+)?)\s*กก", service.detail)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"ไม่เกิน\s*(\d+(?:\.\d+)?)\s*กก", service.detail)
    if m:
        return 0.0, float(m.group(1))
    return None


def _family(name: str) -> str:
    """'อาบน้ำสุนัขขนาดกลาง' -> 'อาบน้ำสุนัข' (services that differ only by size)."""
    return re.sub(r"ขนาด(เล็ก|กลาง|ใหญ่)$", "", name)


def time_slots(kb: KnowledgeBase, step_minutes: int = SLOT_MINUTES) -> list[str]:
    """['09:00', '10:00', ..., '18:00'] - every bookable start time, one per hour from opening
    to closing time (team decision 16 Oct 2026: the last queue of the day starts at 18:00)."""
    open_t, close_t = opening_hours(kb)
    t = datetime.combine(date.min, open_t)
    end = datetime.combine(date.min, close_t)
    slots = []
    while t <= end:
        slots.append(t.strftime("%H:%M"))
        t += timedelta(minutes=step_minutes)
    return slots


def last_start(kb: KnowledgeBase) -> str:
    return time_slots(kb)[-1]


def booking_options(kb: KnowledgeBase, cat_pricing: CatPricing | None = None) -> dict:
    open_t, close_t = opening_hours(kb)
    return {
        "open": open_t.strftime("%H:%M"),
        "close": close_t.strftime("%H:%M"),
        "last_start": last_start(kb),
        "time_slots": time_slots(kb),
        "cat_pricing": cat_pricing.to_public(kb) if cat_pricing else None,
        "cancel_notice_hours": cancel_notice_hours(kb),
        "max_days_ahead": MAX_DAYS_AHEAD,
        "pet_types": ["สุนัข", "แมว"],
        "services": [
            {
                "id": s.id,
                "name": s.name,
                "price": s.price,
                "detail": s.detail,
                "pet_type": s.pet_type,
                "pet_type_confirmed": s.pet_type_confirmed,
                "weight_range_kg": weight_range(s),
            }
            for s in kb.services
        ],
    }


# ------------------------------------------------------------------ validation


@dataclass
class BookingCheck:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    services: list[Service] = field(default_factory=list)
    cat_size: str | None = None
    coat_length: str | None = None
    extra_items: list[dict] = field(default_factory=list)  # cat size / coat surcharges

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def total(self) -> int:
        return sum(s.price for s in self.services) + sum(i["amount"] for i in self.extra_items)


def normalize_contact(contact: str) -> str:
    return re.sub(r"[\s\-()]", "", contact).lower()


def within_opening_hours(t: time, kb: KnowledgeBase) -> bool:
    """A start time must be one of the hourly slots (09:00, 10:00, ... 18:00)."""
    t = t.replace(tzinfo=None, second=0, microsecond=0)
    return t.strftime("%H:%M") in time_slots(kb)


def slot_key(t: time | str) -> str:
    """The hourly slot a start time belongs to: '14:30' -> '14:00'. Bookings made when the form
    offered half-hour times still hold the hour they start in."""
    if isinstance(t, str):
        t = time.fromisoformat(t)
    return f"{t.hour:02d}:00"


def free_slots(kb: KnowledgeBase, day: date, now: datetime, taken: dict[str, int], capacity: int = 1) -> list[str]:
    """Hourly start times on `day` that are still in the future and below capacity."""
    out = []
    for s in time_slots(kb):
        start = datetime.combine(day, time.fromisoformat(s), tzinfo=now.tzinfo)
        if start <= now or taken.get(s, 0) >= capacity:
            continue
        out.append(s)
    return out


def group_ranges(slots: list[str]) -> list[tuple[str, str]]:
    """['09:00','10:00','11:00','13:00'] -> [('09:00','11:00'), ('13:00','13:00')] (consecutive hours)."""
    ranges: list[list[str]] = []
    for s in slots:
        h = int(s[:2])
        if ranges and int(ranges[-1][1][:2]) + SLOT_MINUTES // 60 == h:
            ranges[-1][1] = s
        else:
            ranges.append([s, s])
    return [(a, b) for a, b in ranges]


def slot_taken_error(day: date, slot: str) -> str:
    return f"{SLOT_TAKEN_MESSAGE} (วันที่ {day.isoformat()} เวลา {slot} น.) กรุณาเลือกเวลาอื่น"


class SlotTakenError(Exception):
    """Raised when a slot is full at the moment of writing (checked inside the store lock)."""


def validate_request(
    req,
    kb: KnowledgeBase,
    now: datetime,
    cat_pricing: CatPricing | None = None,
    taken: dict[str, int] | None = None,
    capacity: int = 1,
) -> BookingCheck:
    """req is a BookingCreate. `now` must be timezone-aware in the store's timezone.
    `taken` = active bookings per hourly slot on req.date (from BookingStore.slot_counts)."""
    check = BookingCheck()
    by_id = {s.id: s for s in kb.services}

    ids = [i.strip().upper() for i in req.service_ids]
    if len(set(ids)) != len(ids):
        check.errors.append("เลือกบริการซ้ำกัน")
    for sid in dict.fromkeys(ids):
        svc = by_id.get(sid)
        if svc is None:
            check.errors.append(f"ไม่พบบริการรหัส {sid}")
            continue
        check.services.append(svc)

        if svc.pet_type_confirmed and svc.pet_type != req.pet_type:
            check.errors.append(f"บริการ {svc.id} {svc.name} สำหรับ{svc.pet_type}เท่านั้น")
        elif not svc.pet_type_confirmed:
            check.warnings.append(f"บริการ {svc.id} {svc.name}: ต้องให้พนักงานยืนยันว่ารับ{req.pet_type}")

        rng = weight_range(svc)
        if rng is None:
            continue
        if req.weight_kg is None:
            check.errors.append(f"บริการ {svc.id} {svc.name} ต้องระบุน้ำหนักสัตว์เลี้ยง")
            continue
        low, high = rng
        if not (low < req.weight_kg <= high):
            check.errors.append(
                f"น้ำหนัก {req.weight_kg:g} กก. ไม่อยู่ในเงื่อนไขของ {svc.id} {svc.name} ({svc.detail})"
            )
            fits = [
                s for s in kb.services
                if _family(s.name) == _family(svc.name)
                and (r := weight_range(s)) is not None and r[0] < req.weight_kg <= r[1]
            ]
            check.suggestions += [s.id for s in fits]
            if not fits:
                check.errors.append("ยังไม่มีบริการสำหรับน้ำหนักนี้ในรายการของร้าน กรุณาติดต่อพนักงาน")

    open_t, close_t = opening_hours(kb)
    appt_time = req.time.replace(tzinfo=None, second=0, microsecond=0)
    if not within_opening_hours(appt_time, kb):
        check.errors.append(
            f"ร้านเปิด {open_t.strftime('%H:%M')}–{close_t.strftime('%H:%M')} น. เวลาเริ่มนัดต้องอยู่ระหว่าง "
            f"{open_t.strftime('%H:%M')}–{last_start(kb)} น. ทุก 1 ชั่วโมง (เวลาที่เลือก {appt_time.strftime('%H:%M')} น.) "
            "กรุณาเลือกเวลาในช่วงนี้"
        )
    elif taken and taken.get(slot_key(appt_time), 0) >= capacity:
        check.errors.append(slot_taken_error(req.date, slot_key(appt_time)))
    appointment = datetime.combine(req.date, appt_time, tzinfo=now.tzinfo)
    if appointment <= now:
        check.errors.append("วันและเวลาที่เลือกผ่านไปแล้ว")
    elif req.date > now.date() + timedelta(days=MAX_DAYS_AHEAD):
        check.errors.append(f"จองล่วงหน้าได้ไม่เกิน {MAX_DAYS_AHEAD} วัน")

    _check_cat_details(req, check, cat_pricing)
    check.suggestions = list(dict.fromkeys(check.suggestions))
    return check


def _check_cat_details(req, check: BookingCheck, pricing: CatPricing | None) -> None:
    """Cat size / coat length (from the photo estimate or chosen by hand) -> surcharges."""
    size, coat = getattr(req, "cat_size", None), getattr(req, "coat_length", None)
    if req.pet_type != "แมว":
        if size or coat:
            check.errors.append("ขนาดแมวและลักษณะขนใช้ได้กับการจองของแมวเท่านั้น")
        return
    if pricing is None:
        return
    if size is None and req.weight_kg is not None:
        size = pricing.size_for_weight(req.weight_kg)
    check.cat_size, check.coat_length = size, coat
    if any(s.id == pricing.base_service_id for s in check.services):
        check.extra_items = cat_surcharges(pricing, size, coat)


# ------------------------------------------------------------------ storage


SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    pet_type TEXT NOT NULL,
    pet_name TEXT,
    weight_kg REAL,
    service_ids TEXT NOT NULL,
    estimated_total INTEGER NOT NULL,
    appt_date TEXT NOT NULL,
    appt_time TEXT NOT NULL,
    contact_name TEXT NOT NULL,
    contact TEXT NOT NULL,
    contact_key TEXT NOT NULL,
    note TEXT,
    staff_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cat_size TEXT,
    coat_length TEXT,
    extra_items TEXT
)
"""
# Columns added after the first release; older bookings.db files are upgraded on start.
MIGRATIONS = {"cat_size": "TEXT", "coat_length": "TEXT", "extra_items": "TEXT"}


class BookingStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as db:
            db.execute(SCHEMA)
            have = {r["name"] for r in db.execute("PRAGMA table_info(bookings)")}
            for col, kind in MIGRATIONS.items():
                if col not in have:
                    db.execute(f"ALTER TABLE bookings ADD COLUMN {col} {kind}")

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def new_id(db: sqlite3.Connection, appt_date: date) -> str:
        """PET-BOOK-DDMMYYYYnn: appointment date + running number for that date (01, 02, ... 99, 100).

        Must be called while holding the store lock so two requests never get the same number.
        """
        prefix = f"{ID_PREFIX}{appt_date:%d%m%Y}"
        rows = db.execute("SELECT id FROM bookings WHERE id LIKE ?", (prefix + "%",)).fetchall()
        used = [int(r["id"][len(prefix):]) for r in rows if r["id"][len(prefix):].isdigit()]
        return f"{prefix}{max(used, default=0) + 1:02d}"

    def slot_counts(self, day: date, exclude_id: str | None = None, db: sqlite3.Connection | None = None) -> dict[str, int]:
        """Active requests (pending + confirmed) per hourly slot on `day`."""
        sql = "SELECT id, appt_time FROM bookings WHERE appt_date=? AND status IN ('pending', 'confirmed')"
        if db is None:
            with self._connect() as conn:
                rows = conn.execute(sql, (day.isoformat(),)).fetchall()
        else:
            rows = db.execute(sql, (day.isoformat(),)).fetchall()
        counts: dict[str, int] = {}
        for r in rows:
            if r["id"] != exclude_id:
                k = slot_key(r["appt_time"])
                counts[k] = counts.get(k, 0) + 1
        return counts

    def create(self, req, check: BookingCheck, now: datetime, capacity: int | None = None) -> dict:
        row = {
            "status": "pending",
            "pet_type": req.pet_type,
            "pet_name": (req.pet_name or "").strip() or None,
            "weight_kg": req.weight_kg,
            "service_ids": json.dumps([s.id for s in check.services]),
            "estimated_total": check.total,
            "appt_date": req.date.isoformat(),
            "appt_time": req.time.strftime("%H:%M"),
            "contact_name": req.contact_name.strip(),
            "contact": req.contact.strip(),
            "contact_key": normalize_contact(req.contact),
            "note": (req.note or "").strip() or None,
            "staff_note": None,
            "cat_size": check.cat_size,
            "coat_length": check.coat_length,
            "extra_items": json.dumps(check.extra_items, ensure_ascii=False) if check.extra_items else None,
        }
        with self._lock, self._connect() as db:
            if capacity is not None:  # re-check: another request may have taken the slot since validation
                slot = slot_key(req.time)
                if self.slot_counts(req.date, db=db).get(slot, 0) >= capacity:
                    raise SlotTakenError(slot_taken_error(req.date, slot))
            bid = self.new_id(db, req.date)
            stamp = now.isoformat(timespec="seconds")
            row.update(id=bid, created_at=stamp, updated_at=stamp)
            cols = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            db.execute(f"INSERT INTO bookings ({cols}) VALUES ({marks})", list(row.values()))
        return self.get(bid)

    def get(self, booking_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM bookings WHERE id=?", (booking_id.strip().upper(),)).fetchone()
        return dict(row) if row else None

    def get_for_customer(self, booking_id: str, contact: str) -> dict | None:
        """Only returns the booking when the contact matches (no enumeration of others)."""
        row = self.get(booking_id)
        if row and secrets.compare_digest(row["contact_key"], normalize_contact(contact)):
            return row
        return None

    def list(self, status: str | None = None, limit: int = 200) -> list[dict]:
        sql, args = "SELECT * FROM bookings", []
        if status:
            sql, args = sql + " WHERE status=?", [status]
        sql += " ORDER BY appt_date, appt_time LIMIT ?"
        with self._connect() as db:
            return [dict(r) for r in db.execute(sql, [*args, limit]).fetchall()]

    def list_between(self, date_from: date, date_to: date) -> list[dict]:
        """Every booking with an appointment date in [date_from, date_to], in time order."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM bookings WHERE appt_date BETWEEN ? AND ? ORDER BY appt_date, appt_time, created_at",
                (date_from.isoformat(), date_to.isoformat()),
            ).fetchall()
        return [dict(r) for r in rows]

    def status_totals(self) -> dict[str, int]:
        """All-time count per status, straight from the database."""
        with self._connect() as db:
            rows = db.execute("SELECT status, COUNT(*) AS n FROM bookings GROUP BY status").fetchall()
        counts = {s: 0 for s in STATUS_LABELS}
        counts.update({r["status"]: r["n"] for r in rows})
        return counts

    def set_status(
        self, booking_id: str, status: str, now: datetime, staff_note: str | None = None, capacity: int | None = None
    ) -> dict | None:
        with self._lock, self._connect() as db:
            if capacity is not None and status in ACTIVE_STATUSES:
                row = db.execute("SELECT * FROM bookings WHERE id=?", (booking_id.strip().upper(),)).fetchone()
                if row and row["status"] not in ACTIVE_STATUSES:  # re-activating frees nothing, so check the slot
                    day, slot = date.fromisoformat(row["appt_date"]), slot_key(row["appt_time"])
                    if self.slot_counts(day, exclude_id=row["id"], db=db).get(slot, 0) >= capacity:
                        raise SlotTakenError(slot_taken_error(day, slot))
            cur = db.execute(
                "UPDATE bookings SET status=?, staff_note=COALESCE(?, staff_note), updated_at=? WHERE id=?",
                (status, staff_note, now.isoformat(timespec="seconds"), booking_id.strip().upper()),
            )
            if cur.rowcount == 0:
                return None
        return self.get(booking_id)


def appointment_at(row: dict, tz: tzinfo) -> datetime:
    return datetime.combine(date.fromisoformat(row["appt_date"]), time.fromisoformat(row["appt_time"]), tzinfo=tz)


def mask_contact(contact: str) -> str:
    """'0812345678' -> '08xxxxx678' so a status page never shows the full contact."""
    c = contact.strip()
    if len(c) <= 4:
        return "x" * len(c)
    return c[:2] + "x" * (len(c) - 5) + c[-3:]
