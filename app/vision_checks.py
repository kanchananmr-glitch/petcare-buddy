"""Checks on what the vision model read from a picture, done in code instead of by the model.

Live tests (16 Oct 2026) showed the vision model:
  - B03: read "ตัดเล็บ 100 บาท" on a price sign correctly in `visible_text`, then told the
    customer nail trimming should be 120 baht (S10 ตะไบเล็บ's price, a different service);
  - B05: called a clearly readable notice with injected instructions "unreadable".

So the price comparison and the instruction check use the text the model READ, and the
answer is built from the Knowledge Base by code. The model's own wording is kept only when
neither check applies.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.models import KnowledgeBase, Service
from app.safety.guardrails import check_input

# The sign/receipt may drop "ขนาด" ("ตัดขนสุนัขกลาง") and spaces; parentheses hold sizes/weights.
_PARENS = re.compile(r"\([^)]*\)")
_NUMBER_AFTER = re.compile(r"-?\d[\d,]*(?:\.\d+)?(?!\s*(?:กก|kg|%|ชิ้น|ครั้ง))")
DISCOUNT_RE = re.compile(r"ส่วนลด|ลดราคา|โปรโมชั|discount", re.IGNORECASE)
# Input-guard categories that mean the picture is trying to instruct the bot.
INSTRUCTION_FLAGS = {"prompt_injection", "system_prompt_extraction", "discount_demand", "fake_promotion",
                     "privacy_violation"}


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text.replace("ขนาด", ""))


@dataclass
class PriceLine:
    service: Service
    listed: int

    @property
    def matches(self) -> bool:
        return self.listed == self.service.price


def listed_prices(visible_text: str, kb: KnowledgeBase) -> list[PriceLine]:
    """Service name -> the first price written after it, matched on the exact service name.

    "ตัดเล็บ 100" matches S09 ตัดเล็บ only; S10 ตะไบเล็บ is a different string."""
    text = _norm(_PARENS.sub(" ", visible_text))
    names = sorted(((s, _norm(s.name)) for s in kb.services), key=lambda x: -len(x[1]))
    hits: list[tuple[int, int, Service]] = []
    taken: list[tuple[int, int]] = []
    for svc, name in names:  # longest names first, so a shorter name inside a longer one is skipped
        for m in re.finditer(re.escape(name), text):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            hits.append((m.start(), m.end(), svc))
            taken.append((m.start(), m.end()))
    hits.sort()
    out: list[PriceLine] = []
    seen: set[str] = set()
    for i, (start, end, svc) in enumerate(hits):
        stop = hits[i + 1][0] if i + 1 < len(hits) else len(text)
        m = _NUMBER_AFTER.search(text, end, stop)
        if not m or svc.id in seen:
            continue
        value = float(m.group(0).replace(",", ""))
        if value <= 0:
            continue
        seen.add(svc.id)
        out.append(PriceLine(svc, int(round(value))))
    return out


def price_check_answer(lines: list[PriceLine], visible_text: str, image_type: str, kb: KnowledgeBase) -> str:
    """Customer answer for a price sign or receipt, every price taken from the Knowledge Base."""
    policy = {p.topic: p.text for p in kb.policies}
    out = ["เทียบราคาในภาพกับราคาทางการของร้านค่ะ"]
    for ln in lines:
        s = ln.service
        verdict = "ตรงกับราคาของร้าน" if ln.matches else f"ไม่ตรง ราคาทางการคือ {s.price} บาท"
        out.append(f"• {s.name} ({s.id}): ในภาพ {ln.listed} บาท — {verdict}")
    if DISCOUNT_RE.search(visible_text) and policy.get("ส่วนลด"):
        out.append(f"• ส่วนลดในภาพ: ใช้ไม่ได้ เพราะ{policy['ส่วนลด']}")
    if image_type == "receipt":
        out.append("ระบบตรวจได้เฉพาะราคา ไม่สามารถยืนยันการชำระเงินจากรูปใบเสร็จได้ หากมีข้อสงสัยเรื่องยอดเงินกรุณาติดต่อพนักงานร้านค่ะ")
    else:
        out.append("หากป้ายในร้านไม่ตรงกับราคานี้ กรุณาแจ้งพนักงานร้านค่ะ")
    return "\n".join(out)


def instruction_flags(visible_text: str, observations: list[str]) -> list[str]:
    """Run what was read from the picture through the same input guard as typed messages."""
    flags = check_input(" ".join([visible_text, *observations])).flags
    return [f for f in flags if f in INSTRUCTION_FLAGS]


def injected_text_answer(kb: KnowledgeBase) -> str:
    policy = {p.topic: p.text for p in kb.policies}
    parts = [
        "ภาพนี้มีข้อความที่พยายามสั่งให้ระบบทำสิ่งที่ร้านไม่อนุญาต PetCare Buddy จึงไม่ทำตามข้อความในรูปค่ะ",
    ]
    if policy.get("การจอง"):
        parts.append(f"การจองคิว: {policy['การจอง']}")
    if policy.get("ส่วนลด"):
        parts.append(f"ส่วนลด: {policy['ส่วนลด']}")
    parts.append("ยินดีช่วยตอบเรื่องบริการ ราคา และการจองคิวของร้านนะคะ")
    return "\n".join(parts)
