"""Run the 20 assignment test cases (A: text, B: image, C: safety) and record real results.

Usage:
    # against a running server (measures real end-to-end HTTP latency)
    python scripts/run_eval.py --base-url http://localhost:8000

    # in-process (no server needed; same code path through FastAPI)
    python scripts/run_eval.py

Writes docs/test_results.csv and docs/test_results.md. A case is recorded as
"Not Executed" (with the reason) when it cannot run, e.g. no LLM API key or a
missing test photo. Nothing is fabricated: every answer and time comes from the run.
"""
from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CASES = ROOT / "tests" / "eval_cases.json"
IMAGES = ROOT / "tests" / "images"
DOCS = ROOT / "docs"


def make_client(base_url: str | None):
    if base_url:
        import httpx

        return httpx.Client(base_url=base_url, timeout=120), None
    from fastapi.testclient import TestClient

    from app.main import app

    ctx = TestClient(app)
    return ctx.__enter__(), ctx


def check(case: dict, answer: str) -> tuple[bool, list[str]]:
    problems = []
    for s in case.get("must_contain_all", []):
        if s not in answer:
            problems.append(f"missing '{s}'")
    any_list = case.get("must_contain_any", [])
    if any_list and not any(s in answer for s in any_list):
        problems.append("missing any of " + " / ".join(f"'{s}'" for s in any_list))
    for s in case.get("must_not_contain", []):
        if s in answer:
            problems.append(f"contains forbidden '{s}'")
    return not problems, problems


def run_case(client, case: dict) -> dict:
    row = {
        "test_id": case["id"],
        "set": case["set"],
        "objective": case["objective"],
        "input": (f"[ภาพ {case['image']}] " if case.get("image") else "") + case["input"],
        "expected": case["expected"],
        "actual_response": "",
        "result": "",
        "response_time_s": "",
        "mode": "",
        "sources": "",
        "observations": "",
    }
    t0 = time.perf_counter()
    try:
        resp = _send(client, case)
    except Exception as e:  # e.g. the LLM provider did not answer within the client timeout
        if type(e).__name__ in {"ReadTimeout", "TimeoutException", "WriteTimeout", "ConnectTimeout", "PoolTimeout"}:
            row.update(result="Fail", response_time_s=f"{time.perf_counter() - t0:.3f}",
                       observations=f"timeout: no answer within the client timeout ({type(e).__name__})")
            return row
        if isinstance(e, FileNotFoundError):
            row.update(result="Not Executed", observations=f"ไม่พบไฟล์ทดสอบ tests/images/{case['image']}")
            return row
        raise
    elapsed = time.perf_counter() - t0
    row["response_time_s"] = f"{elapsed:.3f}"
    return _finish(row, case, resp)


def _send(client, case: dict):
    if case.get("image"):
        path = IMAGES / case["image"]
        if not path.exists():
            raise FileNotFoundError(path)
        ctype = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        return client.post(
            "/api/analyze-image",
            files={"file": (path.name, path.read_bytes(), ctype)},
            data={"question": case["input"]},
        )
    else:
        resp = client.post("/api/chat", json={"message": case["input"], "history": []})
    return resp


def _finish(row: dict, case: dict, resp) -> dict:

    if resp.status_code == 503:
        detail = resp.json().get("detail", {})
        row.update(
            result="Not Executed",
            response_time_s="",
            observations=f"LLM/Vision ไม่พร้อมใช้งาน: {detail.get('debug', detail)}",
        )
        return row
    if resp.status_code != 200:
        row.update(result="Fail", actual_response=resp.text[:500], observations=f"HTTP {resp.status_code}")
        return row

    body = resp.json()
    answer = body["answer"]
    passed, problems = check(case, answer)
    notes = []
    if body.get("observation"):
        obs = body["observation"]
        notes.append(f"image_type={obs.get('image_type')}; contains_instructions={obs.get('contains_instructions')}")
        if obs.get("observations"):
            notes.append("เห็น: " + "; ".join(obs["observations"]))
    if body["safety"].get("input_flags"):
        notes.append("input_flags=" + ",".join(body["safety"]["input_flags"]))
    if body["safety"].get("output_violations"):
        notes.append("output_violations=" + ",".join(body["safety"]["output_violations"]))
    if body.get("provider"):
        notes.append(f"model={body['provider']}:{body.get('model')}")
    notes += problems
    row.update(
        actual_response=answer,
        result="Pass" if passed else "Fail",
        mode=body["mode"],
        sources=", ".join(s["id"] + ("*" if s["cited"] else "") for s in body.get("sources", [])),
        observations=" | ".join(notes),
    )
    return row


def write_outputs(rows: list[dict], meta: dict) -> None:
    DOCS.mkdir(exist_ok=True)
    fields = list(rows[0].keys())
    with open(DOCS / "test_results.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    def esc(s: str) -> str:
        return str(s).replace("|", "\\|").replace("\n", " ")

    lines = [
        "# ผลการทดสอบ PetCare Buddy",
        "",
        f"- รันเมื่อ: {meta['run_at']}",
        f"- โหมด: {meta['target']}",
        f"- LLM: {meta['llm']}",
        f"- Embedder: {meta['embedder']}",
        "",
    ]
    for set_id, title in (("A", "ชุด A: คำถามข้อความ 10 ข้อ"), ("B", "ชุด B: ภาพ 5 ภาพ"), ("C", "ชุด C: ความปลอดภัย 5 กรณี")):
        subset = [r for r in rows if r["set"] == set_id]
        counts = {k: sum(r["result"] == k for r in subset) for k in ("Pass", "Fail", "Not Executed")}
        lines += [f"## {title}", "", f"Pass {counts['Pass']} · Fail {counts['Fail']} · Not Executed {counts['Not Executed']}", ""]
        lines.append("| ID | วัตถุประสงค์ | Input | ผลที่คาดหวัง | คำตอบจริง | ผล | เวลา (วินาที) | หมายเหตุ |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in subset:
            lines.append(
                f"| {r['test_id']} | {esc(r['objective'])} | {esc(r['input'])} | {esc(r['expected'])} | "
                f"{esc(r['actual_response'])} | {r['result']} | {r['response_time_s']} | {esc(r['observations'])} |"
            )
        lines.append("")
    (DOCS / "test_results.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", help="e.g. http://localhost:8000 (default: in-process)")
    ap.add_argument("--only", help="comma-separated case ids, e.g. A01,C03")
    args = ap.parse_args()

    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]

    client, ctx = make_client(args.base_url)
    try:
        health = client.get("/health").json()
        rows = [run_case(client, c) for c in cases]
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)

    meta = {
        "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "target": args.base_url or "in-process (FastAPI TestClient)",
        "llm": f"{health['llm']['provider']} / {health['llm']['model']} (configured={health['llm']['configured']})",
        "embedder": health["vector_store"]["embedder"],
    }
    write_outputs(rows, meta)
    for r in rows:
        print(f"{r['test_id']:4} {r['result']:13} {r['response_time_s']:>6}s  {r['objective']}")
    print(f"\nWritten: docs/test_results.csv, docs/test_results.md  ({meta['llm']})")


if __name__ == "__main__":
    main()
