"""Spot-check the chatbot's must-do / must-not-do rules against a running server.

    python scripts/rule_spot_check.py --base-url http://localhost:8000            # random 6 rules
    python scripts/rule_spot_check.py --base-url http://localhost:8000 --all      # every rule
    python scripts/rule_spot_check.py --sample 4 --seed 17                        # reproducible pick

Each rule in data/chatbot_rules.json has one probe in tests/rule_probes.json. The script picks
probes at random (seed is printed), sends them to the real API and writes the real answers,
Pass/Fail and response times to docs/rule_check_results.md / .csv. Nothing is filled in by hand.
"""
from __future__ import annotations

import argparse, csv, json, mimetypes, random, re, sys, time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
RULES = json.loads((ROOT / "data" / "chatbot_rules.json").read_text(encoding="utf-8"))
PROBES = json.loads((ROOT / "tests" / "rule_probes.json").read_text(encoding="utf-8"))["probes"]
IMAGES = ROOT / "tests" / "images"


def rule_text(ref: str) -> str:
    m = re.match(r"(\w+)\[(\d+)\]", ref)
    return RULES[m.group(1)][int(m.group(2))]


def check(p: dict, text: str) -> list[str]:
    problems = []
    for s in p.get("must_contain_all", []):
        if s not in text:
            problems.append(f"missing '{s}'")
    anyl = p.get("must_contain_any", [])
    if anyl and not any(s in text for s in anyl):
        problems.append("missing any of " + " / ".join(f"'{s}'" for s in anyl))
    for s in p.get("must_not_contain", []):
        if s in text:
            problems.append(f"contains forbidden '{s}'")
    if p.get("lang_en"):
        thai = len(re.findall(r"[฀-๿]", text))
        latin = len(re.findall(r"[A-Za-z]", text))
        if latin <= thai:
            problems.append(f"not mainly English (latin={latin}, thai={thai})")
    return problems


def run(client: httpx.Client, p: dict) -> dict:
    row = {"probe_id": p["id"], "rule": rule_text(p["rule"]), "input": p.get("input", ""), "answer": "",
           "result": "", "response_time_s": "", "notes": ""}
    t0 = time.perf_counter()
    if p.get("endpoint") == "cat-estimate":
        path = IMAGES / p["image"]
        row["input"] = f"[รูป {p['image']}] POST /api/cat-estimate"
        r = client.post("/api/cat-estimate", files={"file": (path.name, path.read_bytes(), "image/jpeg")})
    elif p.get("endpoint") == "analyze-image":
        path = IMAGES / p["image"]
        row["input"] = f"[รูป {p['image']}] {p['input']}"
        ctype = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        r = client.post("/api/analyze-image", files={"file": (path.name, path.read_bytes(), ctype)},
                        data={"question": p["input"]})
    else:
        r = client.post("/api/chat", json={"message": p["input"], "history": []})
    row["response_time_s"] = f"{time.perf_counter() - t0:.3f}"
    if r.status_code != 200:
        row.update(result="Not Executed" if r.status_code == 503 else "Fail",
                   answer=r.text[:300], notes=f"HTTP {r.status_code}")
        return row
    body = r.json()
    if p.get("endpoint") == "cat-estimate":
        problems = []
        price = body.get("price") or {}
        if body.get("weight_kg_min") is None or body.get("weight_kg_max") is None:
            problems.append("no weight range")
        if body.get("confidence") not in ("ต่ำ", "กลาง", "สูง"):
            problems.append("no confidence level")
        if "พนักงานจะยืนยันราคาสุดท้าย" not in body.get("message", ""):
            problems.append("no staff-confirmation note")
        items = price.get("items", [])
        if not items or items[0].get("amount") != 500 or sum(i["amount"] for i in items) != price.get("total"):
            problems.append("price is not S04 500 + surcharges")
        row["answer"] = body.get("message", "") + f" [confidence={body.get('confidence')}]"
    else:
        row["answer"] = body.get("answer", "")
        problems = check(p, row["answer"])
        flags = body.get("safety", {}).get("input_flags") or []
        if flags:
            problems_note = "input_flags=" + ",".join(flags)
            row["notes"] = problems_note
    row["result"] = "Fail" if problems else "Pass"
    row["notes"] = " | ".join(x for x in [row["notes"], *problems] if x)
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--sample", type=int, default=6)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    seed = a.seed if a.seed is not None else random.randrange(1, 10_000)
    probes = PROBES if a.all else random.Random(seed).sample(PROBES, min(a.sample, len(PROBES)))
    print(("all rules" if a.all else f"random sample of {len(probes)} rules, seed={seed}"))
    with httpx.Client(base_url=a.base_url, timeout=120) as c:
        health = c.get("/health").json()
        rows = []
        for p in probes:
            row = run(c, p)
            rows.append(row)
            print(f"{row['probe_id']:<5} {row['result']:<12} {row['response_time_s']:>7}s  {row['rule'][:60]}")
    out = ROOT / "docs"
    with open(out / "rule_check_results.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader(); w.writerows(rows)
    esc = lambda s: str(s).replace("|", "/").replace("\n", " ")
    llm = health.get("llm", {})
    lines = [
        "# ผลสุ่มทดสอบข้อกำหนดต้องทำ/ห้ามทำ",
        "",
        f"รันเมื่อ {datetime.now():%Y-%m-%d %H:%M} · {('ทุกข้อ' if a.all else f'สุ่ม {len(probes)} ข้อ seed={seed}')} · "
        f"LLM {llm.get('provider')} / {llm.get('model')} · ผ่าน {sum(r['result']=='Pass' for r in rows)}/{len(rows)}",
        "",
        "| Probe | กฎ | คำถาม | คำตอบจริง | ผล | เวลา (วินาที) | หมายเหตุ |",
        "|---|---|---|---|---|---|---|",
        *[f"| {r['probe_id']} | {esc(r['rule'])} | {esc(r['input'])} | {esc(r['answer'])} | {r['result']} | {r['response_time_s']} | {esc(r['notes']) or '-'} |" for r in rows],
    ]
    (out / "rule_check_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Written: docs/rule_check_results.md, docs/rule_check_results.csv")


if __name__ == "__main__":
    sys.exit(main())
