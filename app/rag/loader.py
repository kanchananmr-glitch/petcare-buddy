"""Load, validate, normalize and chunk the PetCare Grooming Knowledge Base."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.models import FAQ, BusinessProfile, KnowledgeBase, Policy, Service


@dataclass
class Chunk:
    id: str  # stable source id, e.g. "service:S04", "policy:การยกเลิก"
    title: str
    text: str
    kind: str  # service | service_group | policy | faq | profile
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_text(text: str) -> str:
    """NFC-normalize Thai text and collapse whitespace."""
    text = unicodedata.normalize("NFC", str(text))
    text = text.replace("​", "")  # zero-width space, common in Thai copy-paste
    return re.sub(r"\s+", " ", text).strip()


def _pet_type_from_name(name: str) -> str | None:
    if "สุนัข" in name:
        return "สุนัข"
    if "แมว" in name:
        return "แมว"
    return None


def load_knowledge_base(
    knowledge_path: Path,
    drafts_path: Path | None = None,
    include_drafts: bool = False,
) -> KnowledgeBase:
    """Load knowledge.json, optionally merge confirmed draft additions, validate."""
    raw = json.loads(Path(knowledge_path).read_text(encoding="utf-8"))

    profile = BusinessProfile(
        business=normalize_text(raw["business"]),
        address=normalize_text(raw.get("address", "")),
        hours=normalize_text(raw["hours"]),
        contact=normalize_text(raw.get("contact", "")),
    )

    services: list[Service] = []
    for s in raw["services"]:
        name = normalize_text(s["name"])
        detail = normalize_text(s.get("detail", ""))
        pet = _pet_type_from_name(name) or ("สุนัข" if "สุนัข" in detail else None)
        services.append(
            Service(
                id=s["id"].strip().upper(),
                name=name,
                price=int(s["price"]),
                detail=detail,
                pet_type=pet or "ไม่ระบุในข้อมูลร้าน",
                pet_type_confirmed=pet is not None,
            )
        )

    policies = [Policy(topic=normalize_text(p["topic"]), text=normalize_text(p["text"])) for p in raw["policies"]]
    faq = [FAQ(q=normalize_text(f["q"]), a=normalize_text(f["a"])) for f in raw["faq"]]

    if include_drafts and drafts_path and Path(drafts_path).exists():
        drafts = json.loads(Path(drafts_path).read_text(encoding="utf-8"))
        bp = drafts.get("business_profile", {})
        profile.description = normalize_text(bp.get("description", "")) or None
        profile.target_customers = [normalize_text(t) for t in bp.get("target_customers", [])]
        profile.limitations = [normalize_text(t) for t in bp.get("limitations", [])]
        for sid, pet in drafts.get("service_pet_types", {}).items():
            for svc in services:
                if svc.id == sid and not svc.pet_type_confirmed:
                    svc.pet_type = normalize_text(pet)
        existing_topics = {p.topic for p in policies}
        for p in drafts.get("policies", []):
            if normalize_text(p["topic"]) not in existing_topics:  # never override official policy
                policies.append(Policy(topic=normalize_text(p["topic"]), text=normalize_text(p["text"]), draft=True))
        existing_q = {f.q for f in faq}
        for f in drafts.get("faq", []):
            if normalize_text(f["q"]) not in existing_q:
                faq.append(FAQ(q=normalize_text(f["q"]), a=normalize_text(f["a"]), draft=True))

    return KnowledgeBase(
        profile=profile, services=services, policies=policies, faq=faq, drafts_included=include_drafts
    )


def build_chunks(kb: KnowledgeBase, extra: list[Chunk] | None = None) -> list[Chunk]:
    """Split the KB into retrieval chunks: one per entry plus generated group summaries."""
    chunks: list[Chunk] = []
    p = kb.profile

    profile_lines = [
        f"ชื่อร้าน: {p.business}",
        f"เวลาเปิด-ปิด: {p.hours}",
        f"ช่องทางติดต่อ: {p.contact}",
        f"ที่ตั้งร้าน: {p.address}",
    ]
    if p.description:
        profile_lines.append(f"รายละเอียดธุรกิจ: {p.description}")
    chunks.append(
        Chunk("profile:hours_contact", "ข้อมูลร้าน เวลาเปิด-ปิด และช่องทางติดต่อ", " | ".join(profile_lines), "profile")
    )
    if p.target_customers or p.limitations:
        text = "กลุ่มลูกค้าเป้าหมาย: " + "; ".join(p.target_customers) + " | ข้อจำกัดของร้าน: " + "; ".join(p.limitations)
        chunks.append(Chunk("profile:scope", "กลุ่มลูกค้าและข้อจำกัดของร้าน", text, "profile", {"draft": True}))

    for s in kb.services:
        text = (
            f"บริการ {s.id}: {s.name} ราคา {s.price} บาท เงื่อนไข/รายละเอียด: {s.detail or '-'} "
            f"| สัตว์เลี้ยงที่รับ: {s.pet_type}"
        )
        chunks.append(
            Chunk(f"service:{s.id}", f"{s.id} {s.name}", text, "service", {"service_id": s.id, "price": s.price})
        )

    # Group summaries help questions such as "ราคาตัดขนสุนัข" that span several entries.
    groups = {
        "service_group:dog_bath": ("ราคาอาบน้ำสุนัขแยกตามน้ำหนัก", lambda s: s.name.startswith("อาบน้ำสุนัข")),
        "service_group:dog_haircut": ("ราคาตัดขนสุนัขแยกตามน้ำหนัก", lambda s: s.name.startswith("ตัดขนสุนัข")),
        "service_group:cat": ("บริการสำหรับแมว", lambda s: "แมว" in s.name),
        "service_group:addon": (
            "บริการเสริม (เล็บ หู ฟัน ขน สปา)",
            lambda s: s.id in {"S08", "S09", "S10", "S11", "S12", "S13", "S14", "S15"},
        ),
    }
    for gid, (title, pred) in groups.items():
        members = [s for s in kb.services if pred(s)]
        if not members:
            continue
        text = title + ": " + "; ".join(f"{s.id} {s.name} {s.price} บาท ({s.detail})" for s in members)
        chunks.append(Chunk(gid, title, text, "service_group", {"service_ids": [s.id for s in members]}))

    for pol in kb.policies:
        chunks.append(
            Chunk(f"policy:{pol.topic}", f"นโยบาย{pol.topic}", f"นโยบาย{pol.topic}: {pol.text}", "policy", {"draft": pol.draft})
        )
    for i, f in enumerate(kb.faq, start=1):
        chunks.append(
            Chunk(f"faq:{i:02d}", f"FAQ: {f.q}", f"คำถามที่พบบ่อย: {f.q} คำตอบ: {f.a}", "faq", {"draft": f.draft})
        )
    chunks.extend(extra or [])
    return chunks


def kb_fingerprint(chunks: list[Chunk], backend: str) -> str:
    payload = json.dumps([c.to_dict() for c in chunks], ensure_ascii=False, sort_keys=True) + backend
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
