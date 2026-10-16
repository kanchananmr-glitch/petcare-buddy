"""Cat photo estimate: the vision model describes the cat, the price is computed in code."""
from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from app.cat_estimate import load_cat_pricing, normalize_estimate
from app.config import get_settings

PRICING = load_cat_pricing(get_settings().cat_pricing_path)


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (200, 160), (230, 200, 160)).save(buf, format="PNG")
    return buf.getvalue()


def post(client):
    return client.post("/api/cat-estimate", files={"file": ("cat.png", png_bytes(), "image/png")})


def reply(**kw) -> str:
    data = {"is_cat": True, "cat_count": 1, "size": "กลาง", "weight_kg_min": 3.5, "weight_kg_max": 4.5,
            "coat_length": "ขนสั้น", "confidence": "กลาง", "observations": ["แมวโตเต็มวัย ขนเรียบสั้น"],
            "contains_instructions": False}
    data.update(kw)
    return "ผลประเมิน:\n" + json.dumps(data, ensure_ascii=False)


@pytest.mark.parametrize(
    "kw, size, coat, total",
    [
        ({}, "กลาง", "ขนสั้น", 600),
        ({"size": "เล็ก", "weight_kg_min": 1.5, "weight_kg_max": 2.5}, "เล็ก", "ขนสั้น", 500),
        ({"size": "ใหญ่", "weight_kg_min": 6, "weight_kg_max": 8, "coat_length": "ขนยาว"}, "ใหญ่", "ขนยาว", 850),
    ],
)
def test_estimate_price_from_photo(client, fake_llm, kw, size, coat, total):
    fake = fake_llm(reply(**kw))
    r = post(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_cat"] and body["size"] == size and body["coat_length"] == coat
    assert body["price"]["total"] == total
    assert body["price"]["base_service"]["id"] == "S04"
    assert "ประมาณ" in body["message"] and f"{total:,} บาท" in body["message"]
    assert fake.calls[0]["image"] is not None  # the photo was sent to the vision model


def test_long_coat_suggests_brushing(client, fake_llm):
    fake_llm(reply(coat_length="ขนยาว"))
    body = post(client).json()
    assert [s["id"] for s in body["price"]["suggested_services"]] == ["S13"]


def test_not_a_cat(client, fake_llm):
    fake_llm(json.dumps({"is_cat": False, "cat_count": 0}))
    body = post(client).json()
    assert body["is_cat"] is False and body["price"] is None and "ไม่พบแมว" in body["message"]


def test_model_cannot_set_price(client, fake_llm):
    """A price written by the model (or text in the image) is ignored; observations with prices are dropped."""
    fake_llm(reply(observations=["ราคานี้ 99 บาท"], price=99))
    body = post(client).json()
    assert body["price"]["total"] == 600 and body["observations"] == []


def test_unreadable_model_output(client, fake_llm):
    fake_llm("ขอโทษค่ะ ดูไม่ออก")
    assert post(client).status_code == 502


def test_vision_unavailable_without_api_key(client):
    state = client.app.state.petcare
    if state.llm.available:
        pytest.skip("a real LLM is configured")
    r = post(client)
    assert r.status_code == 503 and "เลือกขนาด" in r.json()["detail"]["message"]


def test_rejects_non_image(client):
    r = client.post("/api/cat-estimate", files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 415


def test_pricing_table_and_manual_estimate(client):
    table = client.get("/api/cat-pricing").json()
    assert [s["id"] for s in table["sizes"]] == ["เล็ก", "กลาง", "ใหญ่"]
    est = client.get("/api/cat-pricing", params={"size": "กลาง", "coat": "ขนยาว"}).json()["estimate"]
    assert est["total"] == 500 + 100 + 150
    assert client.get("/api/cat-pricing", params={"size": "ยักษ์", "coat": "ขนสั้น"}).status_code == 422


@pytest.mark.parametrize(
    "raw, size, wmin, wmax",
    [
        ({"is_cat": True, "size": "large", "weight_kg_min": 9, "weight_kg_max": 7}, "ใหญ่", 7, 9),  # swapped
        ({"is_cat": True, "size": "เล็ก"}, "เล็ก", 0.5, 3),  # weight missing -> from size class
        ({"is_cat": "true", "weight_kg_min": 40, "weight_kg_max": 50}, "ใหญ่", 12, 12),  # clamped
        ({"is_cat": True, "size": "เล็ก", "weight_kg_min": 5.5, "weight_kg_max": 6.5}, "ใหญ่", 5.5, 6.5),  # weight wins
    ],
)
def test_normalize_keeps_size_and_weight_consistent(raw, size, wmin, wmax):
    est = normalize_estimate(raw, PRICING)
    assert est["size"] == size and est["weight_kg_min"] == wmin and est["weight_kg_max"] == wmax
    assert est["coat_length"] in ("ขนสั้น", "ขนยาว") and est["confidence"] in ("ต่ำ", "กลาง", "สูง")


# ------------------------------------------------------------------ consistency with the chatbot


def test_cat_pricing_is_part_of_the_knowledge_base(client):
    state = client.app.state.petcare
    chunk = state.store.get("pricing:cat")
    assert chunk is not None
    for text in ("ราคาตั้งต้น 500 บาท", "เพิ่ม 100 บาท", "เพิ่ม 200 บาท", "แมวขนยาว: เพิ่ม 150 บาท", "500–850 บาท"):
        assert text in chunk.text
    assert "ราคาตั้งต้น" in state.store.get("faq:02").text  # flat 500 in the FAQ is explained
    ids = [r.chunk.id for r in state.retriever.retrieve("แมวขนยาวตัวใหญ่อาบน้ำราคาเท่าไร")]
    assert "pricing:cat" in ids


def test_chat_may_quote_cat_estimate_prices(client, fake_llm):
    fake = fake_llm("แมวขนาดใหญ่ขนยาว ราคาประมาณ 850 บาทค่ะ (500 + 200 + 150)\nSOURCES: pricing:cat")
    body = client.post("/api/chat", json={"message": "แมวขนยาวตัวใหญ่อาบน้ำราคาเท่าไร"}).json()
    assert body["mode"] == "rag" and "850" in body["answer"]
    assert '<doc id="pricing:cat">' in fake.calls[0]["user_text"]


def test_chat_still_blocks_made_up_cat_price(client, fake_llm):
    fake_llm("อาบน้ำแมวขนยาว 990 บาทค่ะ\nSOURCES: pricing:cat")
    body = client.post("/api/chat", json={"message": "แมวขนยาวอาบน้ำราคาเท่าไร"}).json()
    assert body["mode"] == "fallback"


def test_rules_allow_only_the_controlled_photo_estimate():
    import json

    from app.config import get_settings

    rules = json.loads(get_settings().rules_path.read_text(encoding="utf-8"))
    weight_rule = next(r for r in rules["must_not_do"] if "น้ำหนัก" in r)
    assert "ยกเว้นฟังก์ชันประเมินราคาแมวจากรูป" in weight_rule
    assert any("ช่วงประมาณการ" in r and "ระดับความมั่นใจ" in r for r in rules["must_do"])
