# Submission Checklist — PetCare Buddy (due 17 Oct 2569)

Status as of 16 Oct 2569. ✅ done and verified here · ⏳ needs the team · ⚠️ done but not verified here

## Assignment checklist (ประเด็นการประเมิน)

| Requirement | Status | Evidence |
|---|---|---|
| LLM: answers the test questions correctly | ⏳ | Run `scripts/run_eval.py` with an API key; results go to `docs/test_results.csv` |
| API: every endpoint in the architecture diagram works | ✅ | 19 API endpoints + 4 pages; `tests/test_api.py`, `tests/test_booking.py`, `tests/test_cat_estimate.py` (174 pass) |
| UI: processing state and errors shown | ✅ | `docs/screenshots/03_after_text_question.png`, `05_after_image_question.png` |
| RAG: answers from the KB in Thai | ⚠️ | Retrieval verified (`tests/test_rag.py`, `/api/retrieve`); Thai generation needs an API key |
| Prompt: random must-do / must-not-do checks | ✅ / ⏳ | Rules in `data/chatbot_rules.json` + `app/prompts.py`; guardrail tests pass; LLM-side checks need a key |
| Safety measures | ✅ | `tests/test_safety.py` (43 pass); set C 5/5 Pass; public queue hides booking numbers |
| Business name, target customers, basic info | ✅ / ⏳ | Report §3; target customers are drafts awaiting confirmation |
| FAQ 10 topics | ✅ | `data/knowledge.json`, report §4.3 |
| Chatbot must-do / must-not-do rules | ✅ | Report §4.4 |
| Test records (10 text, 5 image, 5 safety; result + pass/fail + time) | ✅ | Final run 16 Oct 2026 22:21 on MacBook with the submitted code (OpenRouter gpt-4o-mini), manual review: A 10/10, B 5/5, C 5/5; prompt rules 16/16 (`docs/rule_check_results.md`); history of 5 earlier rounds in report §18 |
| Architecture diagram | ✅ | `docs/diagrams/architecture.png/.svg/.mmd` |
| Data flow diagram | ✅ | `docs/diagrams/data_flow.png/.svg/.mmd` |
| Reproducible source code | ✅ / ⏳ | Runs on the team's MacBook; Windows guide in `SETUP_WINDOWS.md` (not yet tried on Windows). Submit the clean zip only - never `.env`, `.venv` or `data/bookings.db` |
| Responsibility table (who / what / output) | ⏳ | Report §16, proposed — team must confirm |
| Written progress log per person | ⏳ | Report §17 — team must fill in |
| Report as Google Doc **and** PDF in a Drive folder link | ⏳ | Google Doc draft created; insert diagrams, then File → Download → PDF |
| Demo video ≤ 4 min | ⏳ | Script in `docs/demo_script.md`; not recorded |

## Before submitting

1. Put the OpenRouter key and an `ADMIN_TOKEN` in `.env`, start the server, open http://localhost:8000 and try a few questions.
2. ~~Add `tests/images/dog.jpg` and `tests/images/cat.jpg`~~ — done (CC0 photos from WordPress Photo Directory).
3. ~~Run `pytest` and `run_eval.py`; copy results into report §18–20~~ — done 16 Oct (pytest 176 passed / 3 skipped; A/B/C in report).
4. Optional: fix A04, A08, A09 (report §21) and re-run `scripts/run_eval.py`; update §18–19 if results change.
5. Confirm or edit `data/knowledge_draft_additions.json`; if accepted, set `KB_INCLUDE_DRAFTS=true` and re-run the tests.
6. Re-take screenshots with a working LLM: `python scripts/capture_ui.py`.
7. Paste the updated `docs/final_report.md` sections into the Google Doc (it still has the 13 Oct text), insert the diagrams and screenshots into the Google Doc; confirm §16–17; export PDF.
8. Record the demo following `docs/demo_script.md`.
9. Put the Google Doc, PDF, source zip (or GitHub link) and video in one Drive folder and share the link.
