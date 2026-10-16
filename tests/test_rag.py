"""RAG pipeline tests: loading, validation, chunking, vector store, retrieval quality."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import get_settings
from app.rag.embeddings import TfidfEmbedder
from app.rag.loader import build_chunks, load_knowledge_base, normalize_text
from app.rag.retriever import Retriever, VectorStore

S = get_settings()


@pytest.fixture(scope="module")
def kb():
    return load_knowledge_base(S.knowledge_path)


@pytest.fixture(scope="module")
def retriever(kb, tmp_path_factory):
    store = VectorStore(TfidfEmbedder(), tmp_path_factory.mktemp("idx"))
    store.build(build_chunks(kb))
    return Retriever(store, top_k=5)


def test_kb_loads_and_keeps_official_values(kb):
    raw = json.loads(Path(S.knowledge_path).read_text(encoding="utf-8"))
    assert len(kb.services) == 16 and len(kb.faq) == 10 and len(kb.policies) == 8
    for original, loaded in zip(raw["services"], kb.services):
        assert loaded.price == original["price"]  # prices never modified
    assert kb.profile.hours == "ทุกวัน 09:00–18:00 น."


def test_pet_type_derived_only_when_stated(kb):
    by_id = {s.id: s for s in kb.services}
    assert by_id["S01"].pet_type == "สุนัข" and by_id["S01"].pet_type_confirmed
    assert by_id["S04"].pet_type == "แมว"
    assert by_id["S16"].pet_type == "สุนัข"  # stated in the detail text
    assert by_id["S09"].pet_type == "ไม่ระบุในข้อมูลร้าน" and not by_id["S09"].pet_type_confirmed


def test_drafts_excluded_by_default_and_merge_without_overriding(kb):
    assert not any(p.draft for p in kb.policies)
    merged = load_knowledge_base(S.knowledge_path, S.drafts_path, include_drafts=True)
    topics = [p.topic for p in merged.policies]
    assert "การเลื่อนนัด" in topics
    assert len(topics) == len(set(topics))
    assert [s.price for s in merged.services] == [s.price for s in kb.services]


def test_validation_rejects_bad_kb(tmp_path):
    bad = json.loads(Path(S.knowledge_path).read_text(encoding="utf-8"))
    bad["services"][0]["price"] = -10
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_knowledge_base(path)


def test_validation_rejects_duplicate_ids(tmp_path):
    bad = json.loads(Path(S.knowledge_path).read_text(encoding="utf-8"))
    bad["services"][1]["id"] = "S01"
    path = tmp_path / "dup.json"
    path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_knowledge_base(path)


def test_normalize_text():
    assert normalize_text("  อาบน้ำ​   แมว \n") == "อาบน้ำ แมว"


def test_chunking_covers_every_entry(kb):
    chunks = build_chunks(kb)
    ids = {c.id for c in chunks}
    assert {f"service:S{i:02d}" for i in range(1, 17)} <= ids
    assert sum(c.kind == "faq" for c in chunks) == 10
    assert sum(c.kind == "policy" for c in chunks) == 8
    assert "service_group:dog_haircut" in ids
    assert len(ids) == len(chunks)  # unique ids


@pytest.mark.parametrize(
    "question, expected",
    [
        ("ร้านเปิดกี่โมง", "faq:01"),
        ("อาบน้ำแมวราคาเท่าไหร่", "service:S04"),
        ("ตัดขนสุนัขขนาดกลางราคาเท่าไหร่", "service:S06"),
        ("สุนัข 12 กิโล อาบน้ำราคาเท่าไร", "service_group:dog_bath"),
        ("ยกเลิกนัดต้องแจ้งก่อนกี่ชั่วโมง", "policy:การยกเลิก"),
        ("จ่ายเงินยังไง", "policy:การชำระเงิน"),
        ("มีโปรโมชั่นไหม", "faq:04"),
        ("ทำความสะอาดหูคืออะไร", "service:S11"),
        ("จองคิวยังไง", "policy:การจอง"),
        ("แปรงฟันหมาราคาเท่าไหร่", "service:S12"),
    ],
)
def test_retrieval_top5_contains_expected_chunk(retriever, question, expected):
    ids = [r.chunk.id for r in retriever.retrieve(question)]
    assert expected in ids, f"{question!r} -> {ids}"


def test_scores_sorted_and_pinned_chunks(retriever):
    res = retriever.retrieve("อาบน้ำแมว", k=3, pinned=["policy:รูปภาพ"])
    scores = [r.score for r in res[:3]]
    assert scores == sorted(scores, reverse=True)
    assert res[-1].chunk.id == "policy:รูปภาพ"


def test_index_persisted_and_reloaded(kb, tmp_path):
    chunks = build_chunks(kb)
    first = VectorStore(TfidfEmbedder(), tmp_path)
    first.build(chunks)
    assert (tmp_path / "faiss.index").exists() and not first.loaded_from_disk
    second = VectorStore(TfidfEmbedder(), tmp_path)
    second.build(chunks)
    assert second.loaded_from_disk
    assert second.search("อาบน้ำแมว", 1)[0].chunk.id == first.search("อาบน้ำแมว", 1)[0].chunk.id
