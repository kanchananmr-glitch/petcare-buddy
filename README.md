# PetCare Buddy — Intelligent Chatbot for Pet Grooming Services

Final project, 06048308 Intelligent Chatbot Development
กาญจนา น้อยหมอ (67076006) · นลินี อัสแสง (68076035)

A Thai customer-service chatbot for **PetCare Grooming** (a fictional business). It answers
from the store's Knowledge Base with Retrieval-Augmented Generation, analyzes customer
photos with a vision model, and blocks unsafe requests before and after the LLM.

```
Browser UI ──► FastAPI ──► input validation ──► input guard ──► FAISS retrieval ──► LLM ──► output guard ──► answer + sources
                    └──► image validation ──────┘                                (Claude or OpenAI, text + vision)
```

> Windows: follow **SETUP_WINDOWS.md** (Thai, step by step).

## 1. Setup on a MacBook

Requires Python 3.10+.

```bash
cd PetCare_Buddy
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt      # local: adds sentence-transformers (downloads PyTorch) and pytest
cp .env.example .env                      # then put ONE API key in .env
```

`.env` — choose a provider:

```ini
LLM_PROVIDER=openrouter              # or anthropic / openai
OPENROUTER_API_KEY=sk-or-...         # or ANTHROPIC_API_KEY / OPENAI_API_KEY
OPENROUTER_MODEL=openai/gpt-4o-mini  # any vision-capable model id from openrouter.ai/models
```

Start the server:

```bash
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 (chat UI) or http://localhost:8000/docs (API docs).
The first start downloads the multilingual embedding model (~470 MB) and builds the FAISS
index in `data/index/`. `GET /health` shows which embedder and LLM are active.

### Booking requests

Customers press **จองคิว** to send a request (pet, weight, services, date/time, phone or LINE).
The request is stored as "รอพนักงานยืนยัน" in `data/bookings.db`; the chatbot never confirms a slot.
Staff open http://localhost:8000/admin with the `ADMIN_TOKEN` from `.env` to confirm or reject;
customers see the result under **ตรวจสถานะ / ยกเลิก** and can cancel up to 3 hours before the appointment.
Appointment start times are hourly from **09:00 to 18:00** (team decision: the last queue starts at 18:00).
One hour holds `BOOKING_SLOT_CAPACITY` active requests (default 1): a second request for a booked hour is refused
("คิวที่คุณเลือกถูกจองแล้ว"), re-checked inside the database lock. The form greys out booked hours using
`GET /api/availability` (times only), and the chat answers "วันนี้/พรุ่งนี้มีคิวว่างไหม" from the bookings database
without calling the LLM. The backend rejects any other time with a Thai message, so other clients cannot bypass it.

Prompt rules can be spot-checked against the live server: `python scripts/rule_spot_check.py --base-url http://localhost:8000 --sample 4`
(random rules, seed printed) or `--all`.

### Storefront queue board and staff dashboard

- `/static/PetCare_main.html` (button in the chat header): services, prices, policies, FAQ and a
  7-day queue board from `GET /api/queue`. The board shows only "คิวที่ 01, 02, …", time, pet type,
  services and status - never booking numbers (they follow a predictable date pattern), names or contacts.
- `/dashboard` (needs `ADMIN_TOKEN`): totals by status, requests per day, popular services,
  estimated revenue and booking numbers per day, from `GET /api/admin/dashboard`.

Booking numbers look like `PET-BOOK-1710202601` (appointment date DDMMYYYY + running number for that date).

### Cat photo estimate (size, weight, coat → price)

Attach a cat photo in the booking form (choose **แมว**) or with the chat chip
**📷 แนบรูปแมวเพื่อประเมินราคา**. The vision model only describes the cat (size class,
weight range, short/long coat); the price is computed in code from `S04 อาบน้ำแมว`
plus the surcharges in `data/cat_pricing.json` (default: medium +100, large +200,
long coat +150 baht — the owner can change these numbers). The result is always an
estimate; staff confirm the final price. Without an API key the photo cannot be analysed,
but size and coat can still be chosen by hand in the form to see the estimate.

`cat_pricing.json` is the single source for cat prices: at start-up it is also added to the
chatbot's Knowledge Base (chunk `pricing:cat`), and the output guard accepts exactly the amounts
the table can produce, so chat answers and the photo estimate always quote the same numbers.
The chatbot rule against guessing weight from photos (`data/chatbot_rules.json`) allows only this
controlled estimate: a weight range with a confidence level, editable by the customer, priced in code.

Without network access to Hugging Face, or with `EMBEDDING_BACKEND=tfidf`, retrieval uses
the TF-IDF character n-gram fallback automatically. Without an API key, the UI shows
"ยังไม่ได้ตั้งค่า LLM": guardrail answers still work, normal questions return a clear 503 message.

## 2. Tests

One command on macOS runs everything (starts the server, pytest, the 20 cases, screenshots):

```bash
bash scripts/run_all.sh          # macOS / Linux
python scripts/run_all.py        # any OS, including Windows
```

```bash
pytest                                  # offline: API, RAG, safety, image validation (no API key needed)
pytest -m llm                           # real vision-model tests (needs an API key)
python scripts/make_test_images.py      # regenerate price sign / receipt / injection images
python scripts/run_eval.py --base-url http://localhost:8000   # the 20 assignment cases
```

`run_eval.py` writes `docs/test_results.csv` and `docs/test_results.md` with the real
answer, Pass/Fail and response time of every case. Cases that cannot run (no API key, missing
photo) are written as **Not Executed** — nothing is filled in by hand.

Set B uses `tests/images/dog.jpg` (Mehraz Morshed) and `tests/images/cat.jpg` (diaz827), CC0 photos from the WordPress Photo Directory, included in the repo.

Screenshots of the live UI: `python scripts/capture_ui.py` (needs `pip install playwright && playwright install chromium`).

## 3. Knowledge Base

| File | Status |
|---|---|
| `data/knowledge.json` | Official data provided by the team — prices, hours and policies are used unchanged |
| `data/knowledge_draft_additions.json` | **Drafts awaiting the store owner's confirmation** (rescheduling, late arrival, booking information, target customers, pet types for S08–S15, 4 FAQs). Not used unless `KB_INCLUDE_DRAFTS=true` |
| `data/chatbot_rules.json` | Must-do / must-not-do rules, escalation cases, the "information unavailable" sentence |

After the owner confirms the drafts: set `KB_INCLUDE_DRAFTS=true` and restart; the index
rebuilds automatically. Drafts can never override an existing official policy or price.

## 4. Project layout

```
app/
  main.py            FastAPI app, /health, static UI, error format
  config.py          settings from environment variables
  models.py          Pydantic models (KB + API)
  state.py           builds KB, vector index and LLM client at startup
  prompts.py         system prompt, context formatting, vision JSON contract
  llm.py             Anthropic / OpenAI client (text + vision)
  routes/chat.py     POST /api/chat
  routes/services.py GET /api/services, /api/services/{id}, /api/faq, /api/policies, /api/retrieve
  routes/vision.py   POST /api/analyze-image
  routes/booking.py  booking requests + staff confirmation
  routes/cat.py      POST /api/cat-estimate, GET /api/cat-pricing
  cat_estimate.py    cat size/weight/coat parsing + price calculation
  booking.py         booking validation (rules read from the KB) and SQLite storage
  rag/loader.py      validate, normalize, chunk
  rag/embeddings.py  Sentence Transformers + TF-IDF fallback
  rag/retriever.py   FAISS vector store (persisted) + retriever
  safety/guardrails.py input guard, output guard, PII redaction
  static/            index.html, style.css, script.js
data/                knowledge.json, knowledge_draft_additions.json, chatbot_rules.json, cat_pricing.json
tests/               pytest suites, eval_cases.json, images/
scripts/             run_eval.py, make_test_images.py, capture_ui.py
docs/                architecture, data flow, API, test cases/results, demo script, report, screenshots
```

## 5. Cloud deployment (Docker + Render)

`Dockerfile` + `render.yaml` deploy the app to Render's free plan (course week 10).
Step-by-step guide in Thai: [DEPLOY_RENDER.md](DEPLOY_RENDER.md).

```bash
docker build -t petcare-buddy .
docker run -p 8000:8000 --env-file .env petcare-buddy
```

`requirements.txt` holds only the runtime packages (no sentence-transformers/torch), so the
server uses the TF-IDF retriever: Render's free plan has 512 MB RAM and Vercel limits a Python
function to 500 MB. Vercel: see [DEPLOY_VERCEL.md](DEPLOY_VERCEL.md). The free plan sleeps after 15 minutes
idle and its disk is temporary, so booking requests are lost on every redeploy or restart.

## 6. Limitations

- The chatbot cannot confirm a slot, take payment or refund. Booking requests wait for staff; capacity per hourly slot is a fixed number (`BOOKING_SLOT_CAPACITY`).
- The input guard is rule-based (Thai/English regular expressions). New phrasings can slip past it;
  they are then handled by the system prompt and the output guard.
- Answer quality depends on the configured LLM. Run `scripts/run_eval.py` with your key and review every answer.
- All business data is fictional.
