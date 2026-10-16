"""Pydantic models for the Knowledge Base and the REST API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------- Knowledge Base


class Service(BaseModel):
    id: str = Field(pattern=r"^S\d{2}$")
    name: str = Field(min_length=1)
    price: int = Field(gt=0, description="ราคาเป็นบาท")
    detail: str = ""
    pet_type: str = "ไม่ระบุในข้อมูลร้าน"
    pet_type_confirmed: bool = True


class Policy(BaseModel):
    topic: str = Field(min_length=1)
    text: str = Field(min_length=1)
    draft: bool = False


class FAQ(BaseModel):
    q: str = Field(min_length=1)
    a: str = Field(min_length=1)
    draft: bool = False


class BusinessProfile(BaseModel):
    business: str
    address: str
    hours: str
    contact: str
    description: str | None = None
    target_customers: list[str] = []
    limitations: list[str] = []


class KnowledgeBase(BaseModel):
    profile: BusinessProfile
    services: list[Service]
    policies: list[Policy]
    faq: list[FAQ]
    drafts_included: bool = False

    @field_validator("services")
    @classmethod
    def unique_ids(cls, services: list[Service]) -> list[Service]:
        ids = [s.id for s in services]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate service ids: {sorted(dupes)}")
        return services

    @property
    def prices(self) -> set[int]:
        return {s.price for s in self.services}


# ---------------------------------------------------------------- API


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=40)

    @field_validator("message")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be blank")
        return v.strip()


class SourceRef(BaseModel):
    id: str
    title: str
    score: float
    excerpt: str
    cited: bool = False


class SafetyReport(BaseModel):
    input_flags: list[str] = []
    output_violations: list[str] = []
    blocked: bool = False


class ChatResponse(BaseModel):
    answer: str
    mode: Literal["rag", "guardrail", "fallback", "availability"]
    sources: list[SourceRef]
    safety: SafetyReport
    provider: str | None = None
    model: str | None = None
    timings_ms: dict[str, float]
    actions: list[str] = Field(default_factory=list, description="UI shortcuts, e.g. open_booking_form")


class ImageObservation(BaseModel):
    image_type: str = "unknown"
    observations: list[str] = []
    visible_text: str = ""
    contains_instructions: bool = False


class ImageAnalysisResponse(BaseModel):
    answer: str
    mode: Literal["rag", "guardrail", "fallback"]
    observation: ImageObservation
    sources: list[SourceRef]
    safety: SafetyReport
    image: dict
    provider: str | None = None
    model: str | None = None
    timings_ms: dict[str, float]


# ---------------------------------------------------------------- Booking requests

from datetime import date as _date, time as _time  # noqa: E402


class BookingCreate(BaseModel):
    pet_type: Literal["สุนัข", "แมว"]
    pet_name: str | None = Field(None, max_length=50)
    weight_kg: float | None = Field(None, gt=0, le=100)
    service_ids: list[str] = Field(min_length=1, max_length=4)
    date: _date
    time: _time
    contact_name: str = Field(min_length=1, max_length=60)
    contact: str = Field(min_length=3, max_length=60, description="เบอร์โทรหรือ LINE ID สำหรับติดต่อกลับ")
    note: str | None = Field(None, max_length=300)
    # Optional, for cats: from the photo estimate (/api/cat-estimate) or chosen by hand.
    cat_size: Literal["เล็ก", "กลาง", "ใหญ่"] | None = None
    coat_length: Literal["ขนสั้น", "ขนยาว"] | None = None

    @field_validator("time")
    @classmethod
    def whole_minutes(cls, v: _time) -> _time:
        # "10:30:00" or "10:30+07:00" from other clients -> plain 10:30 (avoids naive/aware compare errors)
        return v.replace(second=0, microsecond=0, tzinfo=None)

    @field_validator("contact_name", "contact")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


class ContactProof(BaseModel):
    contact: str = Field(min_length=3, max_length=60)


class StaffDecision(BaseModel):
    status: Literal["confirmed", "rejected"]
    staff_note: str | None = Field(None, max_length=300)
