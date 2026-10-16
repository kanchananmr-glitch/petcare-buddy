# Project Plan — PetCare Buddy

## Mandatory requirements (from "Final Project chatbot.docx")

| # | Requirement | Where it is met |
|---|---|---|
| 1 | Answers only from real store data, no invented facts | RAG over `data/knowledge.json`; output guard checks prices and claims |
| 2 | Customers can use it without explanation | Welcome message, suggested questions, clear Thai errors |
| 3 | Cannot be tricked (discounts, other customers' data, non-existent policies) | Input guard + prompt rules + output guard |
| 4 | Business: small service business, ≥ 15 items, plausible images, no real personal data | 16 services; dog/cat photos, price signs, receipts; fictional data |
| 5 | Deliverable 1 components: LLM, UI states, Prompt, API, RAG (Thai), Safety | `app/` |
| 6 | Deliverable 2 document sections incl. role table, 10/5/5 tests with time | `docs/final_report.md` and the Google Doc |
| 7 | Deliverable 3: demo ≤ 4 min | `docs/demo_script.md` |

## Technology decisions

| Decision | Choice | Why |
|---|---|---|
| Backend | FastAPI + Pydantic | Schema validation and `/docs` for free; simple to run on a MacBook |
| Vector store | FAISS `IndexFlatIP` | Local, no service to run; exact search is fine for 40 chunks |
| Embeddings | Multilingual MiniLM, TF-IDF char n-gram fallback | Thai support; the fallback keeps the system working offline and in tests |
| LLM | Claude or OpenAI via `LLM_PROVIDER` | The team chose to support both |
| Frontend | Plain HTML/CSS/JS served by FastAPI | No build step |
| Safety | Deterministic guards on both sides of the LLM | Testable without an API key; does not rely on the model alone |
| KB gaps | Drafts in a separate file, off by default | The brief forbids inventing business facts without confirmation |

## Task breakdown

| Task | Owner (proposed) | State |
|---|---|---|
| KB loading, validation, chunking, FAISS | นลินี | Done |
| Prompt and rules | นลินี | Done |
| Guardrails | นลินี | Done |
| Web UI | นลินี | Done |
| FastAPI endpoints | กาญจนา | Done |
| Vision endpoint and test images | กาญจนา | Done (needs API key run) |
| Diagrams | กาญจนา | Done |
| Tests and evaluation runner | Both | Done; sets A/B need API key |
| Report, demo | Both | Draft report done; demo not recorded |
