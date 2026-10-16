"""Backend safety controls: input screening and output validation.

Defense in depth:
  1. Input guard  - deterministic screening before any LLM call. High-risk requests
                    (prompt injection, prompt extraction, privacy, discounts, fake
                    promotions, diagnosis) get a policy answer built from the KB.
  2. Prompt rules - see app/prompts.py (system prompt + chatbot_rules.json).
  3. Output guard - validates the LLM answer: prices must exist in the KB, no claimed
                    bookings/refunds/payments/discounts, no diagnosis or medication,
                    no system-prompt leakage, personal data redacted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations

from app.models import KnowledgeBase
from app.prompts import CANARY

# --------------------------------------------------------------------- input guard

INPUT_PATTERNS: dict[str, list[str]] = {
    "system_prompt_extraction": [
        r"system\s*prompt",
        r"(คำสั่ง|ข้อความ|การตั้งค่า)\s*(ระบบ|ที่ซ่อน|ภายใน|ตั้งต้น|เริ่มต้น)",
        r"พรอมต์|พรอมท์|พร้อมท์|พร้อมต์",
        r"(reveal|show|print|repeat|display|tell me).{0,40}(instruction|prompt|rules|configuration)",
        r"hidden\s+instruction",
        r"กฎ(ทั้งหมด)?ที่(คุณ|เธอ|บอท)ได้รับ",
    ],
    "prompt_injection": [
        r"ignore\s+(all\s+|the\s+|any\s+)?(previous|above|prior|earlier)",
        r"disregard\s+(all\s+|the\s+)?(previous|above|rules|instructions)",
        r"(ลืม|เพิกเฉย|ไม่ต้องสนใจ|ข้าม|ละเลย)\s*(คำสั่ง|กฎ|ข้อกำหนด)",
        r"developer\s*mode|jailbreak|\bDAN\b|admin\s*override",
        r"you\s+are\s+now|from\s+now\s+on\s+you",
        r"(ตอนนี้|ต่อจากนี้)\s*(คุณ|เธอ|บอท)\s*(คือ|เป็น)",
        r"(ฉัน|ผม|เรา)\s*(เป็น|คือ)\s*(แอดมิน|ผู้ดูแลระบบ|เจ้าของร้าน|ผู้พัฒนา|developer|admin)",
        r"สวมบทบาท|แกล้งทำเป็น|role\s*-?\s*play",
    ],
    "privacy_violation": [
        r"(ข้อมูล|เบอร์|ชื่อ|ที่อยู่|อีเมล|ประวัติ|คิว|การจอง).{0,20}(ลูกค้า\s*(คน|ราย|ท่าน)?\s*(อื่น|ก่อน|ก่อนหน้า))",
        r"(ใคร|ลูกค้าคนไหน|คนไหน).{0,15}(จอง|นัด|มารับบริการ)",
        r"รายชื่อลูกค้า|ฐานข้อมูลลูกค้า",
        r"(other|another|previous)\s+customer",
        r"customer\s+(list|data|database|phone|records?)",
    ],
    "medical_diagnosis": [
        r"วินิจฉัย",
        r"(เป็น|ติด)\s*(โรค|เชื้อ)\s*(อะไร|หรือเปล่า|ไหม|รึเปล่า)",
        r"(ยา|ครีม|แชมพูยา)\s*(อะไร|ตัวไหน|ชนิดไหน|ยี่ห้อไหน)",
        r"(ควร|ต้อง)\s*(กิน|ใช้|ทา|หยอด)\s*ยา",
        r"(ผื่น|แผล|คัน|ขนร่วงเป็นหย่อม|ตุ่ม|แดง|ติ่งเนื้อ).{0,25}(เป็นอะไร|เพราะอะไร|รักษา)",
        r"รักษา\s*(ยังไง|อย่างไร|ได้ไหม|ด้วย)",
        r"diagnos|medication|medicine|what\s+disease",
    ],
    "fake_promotion": [
        r"(เห็น|ได้ยิน|เพื่อนบอก|แอดมินบอก|พนักงานบอก|ประกาศ|โพสต์|เพจ|โฆษณา).{0,40}(โปร|ส่วนลด|ลดราคา|แถม|ฟรี|ครึ่งราคา)",
        r"\d+\s*แถม\s*\d+",
        r"(คูปอง|โค้ดส่วนลด|promo\s*code|coupon|voucher)",
        r"โปร(โมชั่น|โมชัน)?.{0,25}(\d+\s*(%|เปอร์เซ็น)|แถม|ฟรี|ครึ่งราคา)",
        r"ครึ่งราคา",
    ],
    "discount_demand": [
        r"(ขอ|ให้|อยากได้|ช่วย)\s*(ส่วนลด|ลดราคา)",
        r"ลด\s*(ราคา)?\s*ให้\s*(หน่อย|ได้ไหม|ด้วย|ที|นะ)",
        r"ลด\s*\d+\s*(%|เปอร์เซ็น|บาท)",
        r"(ทำ|บริการ|ขอ|อาบ|ตัด).{0,12}ฟรี",
        r"\bdiscount\b|for\s+free|\bfree\s+(service|groom|bath)",
        r"ราคาพิเศษ|ลูกค้าประจำต้องลด",
    ],
}

# Evaluated in this order: the first matching category decides the response.
PRIORITY = [
    "system_prompt_extraction",
    "prompt_injection",
    "privacy_violation",
    "medical_diagnosis",
    "fake_promotion",
    "discount_demand",
]

_COMPILED = {k: [re.compile(p, re.IGNORECASE) for p in v] for k, v in INPUT_PATTERNS.items()}


@dataclass
class InputCheck:
    flags: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.flags)

    @property
    def primary(self) -> str | None:
        return self.flags[0] if self.flags else None


def check_input(text: str) -> InputCheck:
    flags = [cat for cat in PRIORITY if any(p.search(text) for p in _COMPILED[cat])]
    return InputCheck(flags=flags)


def _policy(kb: KnowledgeBase, topic: str) -> str:
    return next((p.text for p in kb.policies if p.topic == topic), "")


def guardrail_response(category: str, kb: KnowledgeBase, unavailable_message: str) -> tuple[str, list[str]]:
    """Return (Thai answer, source chunk ids) for a blocked input category."""
    if category in {"system_prompt_extraction", "prompt_injection"}:
        return (
            "ขออภัยค่ะ PetCare Buddy ไม่สามารถเปิดเผยคำสั่งภายในของระบบ หรือเปลี่ยนแปลงกฎการให้บริการตามคำขอได้ค่ะ "
            "ยินดีช่วยตอบเรื่องบริการ ราคา เวลาเปิด-ปิด และนโยบายของร้านนะคะ 🐾",
            [],
        )
    if category == "privacy_violation":
        return (
            f"ขออภัยค่ะ ไม่สามารถเปิดเผยข้อมูลการจองหรือข้อมูลของลูกค้ารายอื่นได้ค่ะ "
            f"(นโยบายข้อมูลส่วนบุคคล: {_policy(kb, 'ข้อมูลส่วนบุคคล')}) "
            "หากต้องการตรวจสอบคิวของตนเอง กรุณาติดต่อพนักงานร้านโดยตรงนะคะ",
            ["policy:ข้อมูลส่วนบุคคล"],
        )
    if category == "medical_diagnosis":
        return (
            f"ขออภัยค่ะ PetCare Buddy ไม่สามารถวินิจฉัยโรคหรือแนะนำยาได้ค่ะ ({_policy(kb, 'รูปภาพ')}) "
            "หากน้องมีอาการผิดปกติ แนะนำให้พาไปพบสัตวแพทย์ก่อนนะคะ และร้านอาจงดให้บริการชั่วคราวเพื่อความปลอดภัยของน้อง 🩺",
            ["policy:รูปภาพ", "policy:ความปลอดภัย"],
        )
    if category in {"fake_promotion", "discount_demand"}:
        return (
            f"ขออภัยค่ะ ตามข้อมูลของร้าน {_policy(kb, 'ส่วนลด')} จึงไม่สามารถให้ส่วนลดหรือยืนยันโปรโมชันดังกล่าวได้ค่ะ "
            "ราคาบริการเป็นไปตามรายการราคาของร้าน หากมีข้อสงสัยเพิ่มเติมกรุณาติดต่อพนักงานร้านนะคะ",
            ["policy:ส่วนลด", "faq:04"],
        )
    return unavailable_message, []


# --------------------------------------------------------------------- output guard

PRICE_RE = re.compile(r"(?:฿\s*(\d[\d,]*))|(?:(\d[\d,]*)\s*(?:บาท|฿|baht|thb))", re.IGNORECASE)

# Phrases claiming an action was completed. A match is ignored when a negation /
# future marker appears shortly before it ("ยังไม่", "ไม่สามารถ", "จะ", "ต้อง" ...).
CLAIM_PATTERNS = {
    "claimed_booking_confirmation": [
        r"ยืนยัน\s*(การ)?\s*(จอง|คิว|นัด)\S{0,15}(เรียบร้อย|สำเร็จ)\s*แล้ว",
        r"จอง\s*(คิว)?\s*ให้\s*(เรียบร้อย|สำเร็จ)?\s*แล้ว",
        r"booking\s+(is\s+|has\s+been\s+)?confirmed",
    ],
    "claimed_refund": [
        r"(คืนเงิน|โอนเงินคืน)\s*(ให้)?\s*(คุณ)?\s*(เรียบร้อย|สำเร็จ)?\s*แล้ว",
        r"refund\s+(has\s+been\s+)?(processed|issued|completed)",
    ],
    "claimed_payment": [
        r"(ชำระ|จ่าย)\s*เงิน\s*(ของคุณ)?\s*(เรียบร้อย|สำเร็จ)\s*แล้ว",
        r"payment\s+(has\s+been\s+|is\s+)?(received|completed|confirmed)",
    ],
    "claimed_cancellation": [r"(ยกเลิก|เลื่อน)\s*(นัด|คิว|การจอง)\s*(ให้)?\s*(เรียบร้อย|สำเร็จ)\s*แล้ว"],
    "granted_discount": [
        r"(ได้รับ|ให้|มอบ)\s*ส่วนลด\s*\d",
        r"ลด\s*ให้\s*\d",
        r"ส่วนลด\s*\d+\s*(%|เปอร์เซ็น|บาท)",
        r"(discount|off)\s+of\s+\d|\d+\s*%\s*off",
    ],
    "medical_advice": [
        r"(น่าจะ|คง|อาจจะ?)\s*(เป็น|ติด)\s*(โรค|เชื้อ)",
        r"(ควร|แนะนำให้)\s*(กิน|ใช้|ทา|หยอด|ให้)\s*ยา",
        r"\d+\s*(มก\.|mg|มิลลิกรัม)",
    ],
}
# Action claims are excused by negation or future/conditional markers ("พนักงานจะยืนยัน...").
NEGATION_RE = re.compile(r"(ยังไม่|ไม่สามารถ|ไม่ได้|ไม่|จะ|ต้อง|หลังจาก|เมื่อ|ก่อน|cannot|can't|not|won't|will)\s*\S{0,12}$", re.IGNORECASE)
# Discounts and medical advice are excused only by an explicit negation ("ไม่มีส่วนลด 10%").
STRONG_NEGATION_RE = re.compile(r"(ยังไม่|ไม่สามารถ|ไม่ได้|ไม่มี|ไม่|cannot|can't|not|no)\s*\S{0,12}$", re.IGNORECASE)
STRONG_ONLY = {"granted_discount", "medical_advice"}

_CLAIMS = {k: [re.compile(p, re.IGNORECASE) for p in v] for k, v in CLAIM_PATTERNS.items()}

PII_PATTERNS = {
    # (?<!BOOK-): a booking number such as PET-BOOK-0510202601 contains a date, not a phone number
    "phone": re.compile(r"(?<!\d)(?<!BOOK-)0\d{1,2}[-\s]?\d{3}[-\s]?\d{3,4}(?!\d)"),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "thai_id": re.compile(r"(?<!\d)\d[-\s]?\d{4}[-\s]?\d{5}[-\s]?\d{2}[-\s]?\d(?!\d)"),
}

SAFE_FALLBACK = (
    "ขออภัยค่ะ ระบบไม่สามารถให้คำตอบนี้ได้ เนื่องจากคำตอบอาจไม่ตรงกับข้อมูลทางการของร้าน "
    "กรุณาติดต่อเจ้าหน้าที่ร้าน PetCare Grooming เพื่อสอบถามเพิ่มเติมค่ะ"
)


@dataclass
class OutputCheck:
    text: str
    violations: list[str] = field(default_factory=list)
    redactions: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.violations)


def allowed_prices(kb: KnowledgeBase) -> set[int]:
    prices = sorted(kb.prices)
    allowed = set(prices)
    for n in (2, 3):  # totals of multiple services, e.g. bath + nail trim
        allowed |= {sum(c) for c in combinations(prices, n)}
    return allowed


def _negated(text: str, start: int, strong_only: bool = False) -> bool:
    window = text[max(0, start - 20) : start]
    return bool((STRONG_NEGATION_RE if strong_only else NEGATION_RE).search(window))


def validate_output(text: str, kb: KnowledgeBase, extra_allowed_numbers: set[int] | None = None) -> OutputCheck:
    result = OutputCheck(text=text)

    if CANARY in text or "หลักการตอบ:" in text or "<knowledge>" in text:
        result.violations.append("system_prompt_leak")

    ok_prices = allowed_prices(kb) | (extra_allowed_numbers or set())
    for m in PRICE_RE.finditer(text):
        raw = (m.group(1) or m.group(2) or "").replace(",", "")
        if raw.isdigit() and int(raw) not in ok_prices:
            result.violations.append(f"unsupported_price:{raw}")

    for category, patterns in _CLAIMS.items():
        for p in patterns:
            for m in p.finditer(text):
                if not _negated(text, m.start(), strong_only=category in STRONG_ONLY):
                    result.violations.append(category)
                    break

    redacted = text
    for name, p in PII_PATTERNS.items():
        if p.search(redacted):
            redacted = p.sub("[ข้อมูลถูกปกปิด]", redacted)
            result.redactions.append(name)
    result.text = redacted
    result.violations = list(dict.fromkeys(result.violations))  # de-duplicate, keep order
    return result
