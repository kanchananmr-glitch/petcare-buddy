"""Regression tests for eval cases A04, A08, A09.

The real model (gpt-4o-mini) once answered these with "ยังไม่มีข้อมูลดังกล่าว" or dropped the
cat-bath condition even though the KB had the answer. FakeLLM reproduces those replies so the
backend corrections (topic pinning, KB answer, caveat append) are tested offline.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

UNAVAILABLE = "ขออภัยค่ะ ขณะนี้ยังไม่มีข้อมูลดังกล่าวในระบบ กรุณาติดต่อเจ้าหน้าที่ร้าน PetCare Grooming เพื่อสอบถามเพิ่มเติมค่ะ"
CASES = {c["id"]: c for c in json.loads((Path(__file__).parent / "eval_cases.json").read_text(encoding="utf-8"))["cases"]}


def passes(case: dict, answer: str) -> bool:
    return (
        all(s in answer for s in case.get("must_contain_all", []))
        and (not case.get("must_contain_any") or any(s in answer for s in case["must_contain_any"]))
        and not any(s in answer for s in case.get("must_not_contain", []))
    )


@pytest.mark.parametrize("case_id, pinned", [
    ("A04", ["service:S04", "pricing:cat"]),
    ("A08", ["policy:การชำระเงิน", "faq:05"]),
    ("A09", ["policy:ส่วนลด", "faq:04"]),
])
def test_topic_chunks_always_in_context(client, fake_llm, case_id, pinned):
    fake = fake_llm("ok\nSOURCES: none")
    client.post("/api/chat", json={"message": CASES[case_id]["input"]})
    for cid in pinned:
        assert f'<doc id="{cid}">' in fake.calls[0]["user_text"]


def test_cat_context_carries_safety_condition(client, fake_llm):
    fake = fake_llm("ok\nSOURCES: none")
    client.post("/api/chat", json={"message": CASES["A04"]["input"]})
    assert "เฉพาะแมวที่ยอมรับการดูแลได้อย่างปลอดภัย" in fake.calls[0]["user_text"].split('<doc id="pricing:cat">')[1]


@pytest.mark.parametrize("case_id", ["A08", "A09"])
def test_wrong_no_info_answer_is_replaced_from_kb(client, fake_llm, case_id):
    fake_llm(UNAVAILABLE + "\nSOURCES: none")
    body = client.post("/api/chat", json={"message": CASES[case_id]["input"]}).json()
    assert body["mode"] == "rag"
    assert passes(CASES[case_id], body["answer"]), body["answer"]
    assert any(s["cited"] for s in body["sources"])


def test_cat_answer_without_condition_gets_condition(client, fake_llm):
    fake_llm("ราคาอาบน้ำแมวเริ่มต้นที่ 500 บาท แมวขนยาวเพิ่ม 150 บาทค่ะ\nSOURCES: pricing:cat")
    body = client.post("/api/chat", json={"message": CASES["A04"]["input"]}).json()
    assert body["mode"] == "rag"
    assert passes(CASES["A04"], body["answer"]), body["answer"]
    assert body["answer"].count("ปลอดภัย") == 1


def test_good_cat_answer_is_left_alone(client, fake_llm):
    reply = "อาบน้ำแมว 500 บาทค่ะ เฉพาะแมวที่ยอมรับการดูแลได้อย่างปลอดภัย"
    fake_llm(reply + "\nSOURCES: service:S04")
    body = client.post("/api/chat", json={"message": CASES["A04"]["input"]}).json()
    assert body["answer"] == reply


def test_real_unknown_question_still_says_no_info(client, fake_llm):
    """A10 (parking) has no KB entry: the 'no information' answer must stay."""
    fake_llm(UNAVAILABLE + "\nSOURCES: none")
    body = client.post("/api/chat", json={"message": CASES["A10"]["input"]}).json()
    assert body["answer"] == UNAVAILABLE
