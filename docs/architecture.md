# System Architecture — PetCare Buddy

Rendered: [diagrams/architecture.png](diagrams/architecture.png) · [SVG](diagrams/architecture.svg) · source [diagrams/architecture.mmd](diagrams/architecture.mmd)

Every box maps to a file in `app/`; every endpoint shown exists and is covered by `tests/test_api.py`.

```mermaid
flowchart TB
    C([ลูกค้า]) --> UI["Web Chat UI<br/>index.html · style.css · script.js"]

    UI -- "POST /api/chat<br/>(JSON)" --> CHAT["routes/chat.py"]
    UI -- "POST /api/analyze-image<br/>(multipart)" --> VIS["routes/vision.py"]
    UI -- "GET /health" --> H["main.py health()"]
    UI -. "GET /api/services · /api/services/{id}<br/>/api/faq · /api/policies · /api/retrieve" .-> KBAPI["routes/services.py"]
    UI -- "/api/bookings · GET /api/availability<br/>(คิวว่าง: เฉพาะเวลา)" --> BK["routes/booking.py<br/>คำขอจองคิว (ดู booking_flow)"]
    CHAT -- "ถามคิวว่างวันนี้/พรุ่งนี้<br/>(ตอบจากฐานข้อมูล ไม่เรียก LLM)" --> BDB
    STAFF([พนักงานร้าน]) -- "/admin · /dashboard<br/>/api/admin/*" --> BK
    SHOP["หน้าร้าน PetCare_main.html"] -- "GET /api/queue<br/>(ลำดับคิว ไม่มีข้อมูลลูกค้า)" --> BK
    UI -- "ลิงก์หน้าร้าน" --> SHOP
    BK <--> BDB[("Booking DB · SQLite")]
    UI -- "POST /api/cat-estimate<br/>GET /api/cat-pricing" --> CAT["routes/cat.py<br/>validate_image() → โมเดลบรรยายแมว (JSON)<br/>→ โค้ดคิดราคาจากตาราง"]
    CAT <-. "รูป → ขนาด · น้ำหนัก · ขน" .-> EXT
    CP[("cat_pricing.json<br/>ค่าบริการแมว")] --> CAT
    CP --> LOAD
    CP --> BK

    CHAT --> V1["Input Validation<br/>Pydantic · ≤ 1000 ตัวอักษร"]
    VIS --> IV["Image Validation<br/>ชนิดไฟล์จริง · ≤ 5 MB · ย่อภาพ ≤ 1568 px"]
    V1 --> IG{"Input Guard<br/>check_input()"}
    IV --> IG
    IG -- "พบความเสี่ยง" --> GR["Guardrail Response<br/>ตอบจากนโยบายใน KB<br/>(ไม่เรียก LLM)"]
    IG -- "ผ่าน" --> RET["RAG Retrieval<br/>rag/retriever.py · top-k"]
    RET <--> VDB[("Vector Database<br/>FAISS IndexFlatIP")]
    RET --> PR["Prompt Builder<br/>prompts.py + chatbot_rules.json"]
    PR --> LLM["LLM Client · llm.py"]
    LLM <--> EXT["LLM Provider API<br/>OpenRouter / Claude / OpenAI<br/>(text + vision)"]
    LLM --> OG{"Output Guard<br/>validate_output()"}
    OG -- "ผ่าน" --> FIX["ปรับคำตอบจาก KB<br/>(kb_answer · เติมเงื่อนไขบริการ)"]
    FIX --> R1(["คำตอบ + แหล่งอ้างอิง<br/>mode = rag"])
    OG -- "ไม่ผ่าน" --> R2(["คำตอบสำรองที่ปลอดภัย<br/>mode = fallback"])
    GR --> R3(["คำตอบตามนโยบาย<br/>mode = guardrail"])
    R1 --> UI
    R2 --> UI
    R3 --> UI

    KB[("Knowledge Base<br/>data/knowledge.json")] --> LOAD["rag/loader.py<br/>validate · normalize · chunk"]
    DR[("Draft additions<br/>(KB_INCLUDE_DRAFTS=true เท่านั้น)")] -.-> LOAD
    LOAD --> EMB["rag/embeddings.py<br/>Sentence Transformers (multilingual)<br/>หรือ TF-IDF char n-gram"]
    EMB --> VDB
    LOAD --> KBAPI

    classDef store fill:#d6e8f8,stroke:#3d6f9e,color:#233349
    classDef guard fill:#fff3d6,stroke:#b07d00,color:#233349
    classDef out fill:#c8eedc,stroke:#23785e,color:#233349
    classDef ext fill:#f3e8ff,stroke:#7a4fb5,color:#233349
    class KB,DR,VDB,BDB,CP store
    class IG,OG,GR,V1,IV guard
    class R1,R2,R3 out
    class EXT ext
```

## Components

| Layer | File | Responsibility |
|---|---|---|
| Web UI | `app/static/index.html`, `style.css`, `script.js` | Chat, suggested questions, image upload, loading state, error banner with retry, source tags, clear conversation |
| API | `app/main.py`, `app/routes/*.py` | REST endpoints, request validation, error format |
| Input validation | Pydantic models in `app/models.py`; `validate_image()` in `routes/vision.py` | Length, blank, file type by decoding, size, dimensions |
| Input guard | `app/safety/guardrails.py: check_input()` | Regex rules (Thai + English) for prompt extraction, injection/jailbreak, privacy, diagnosis, fake promotions, discount demands |
| Knowledge Base | `data/knowledge.json` (official), `data/knowledge_draft_additions.json` (drafts, off by default), `data/chatbot_rules.json` | Business facts; behavioural rules |
| Loader | `app/rag/loader.py` | Validate (Pydantic), normalize (NFC, whitespace), chunk: 1 per service/policy/FAQ + profile + 4 group summaries + cat pricing table = 40 chunks |
| Embeddings | `app/rag/embeddings.py` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`; automatic fallback to TF-IDF character n-grams if the model cannot load |
| Vector DB | `app/rag/retriever.py` | FAISS `IndexFlatIP` on L2-normalized vectors (cosine), persisted to `data/index/` with a content fingerprint |
| Prompting | `app/prompts.py` | System prompt with rules + canary token; `<knowledge>` context; `SOURCES:` citation line; JSON contract for vision |
| LLM | `app/llm.py` | Anthropic Claude or OpenAI (text + vision), selected by `LLM_PROVIDER` |
| Booking | `app/booking.py`, `app/routes/booking.py`, `static/admin.html` | Booking requests validated against the KB, stored in SQLite (`data/bookings.db`); staff confirm at `/admin`. Flow: [diagrams/booking_flow.png](diagrams/booking_flow.png) |
| Output guard | `guardrails.py: validate_output()` | Prices must exist in the KB (or be sums of 2–3 listed prices); blocks claimed bookings/refunds/payments/cancellations, granted discounts, diagnosis/medication, system-prompt leakage; redacts phone/email/ID numbers |
| Cat estimate | `app/cat_estimate.py`, `app/routes/cat.py`, `data/cat_pricing.json` | Vision model describes the cat (size, weight range, coat) as JSON; price computed in code from S04 + surcharge table; the table is also added to the Knowledge Base as chunk `pricing:cat` |
| Storefront + dashboard | `app/static/PetCare_main.html`, `app/static/dashboard.html` | Public 7-day queue board (queue numbers only) and staff dashboard (`ADMIN_TOKEN`) |
