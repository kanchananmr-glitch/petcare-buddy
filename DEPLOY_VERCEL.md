# Deploy PetCare Buddy บน Vercel

ใช้ FastAPI แบบ zero-config ของ Vercel (ไฟล์ `index.py` เป็นจุดเริ่ม) ทางเลือกหลักของรายวิชาคือ Docker + Render ดู `DEPLOY_RENDER.md`

## สิ่งที่ปรับให้ใช้กับ Vercel ได้

| ปัญหา | วิธีแก้ |
|---|---|
| `LAMBDA_SIZE_EXCEEDED` (ขนาดรวม 5,666 MB) เพราะ `requirements.txt` เดิมมี sentence-transformers และ PyTorch | `requirements.txt` เหลือเฉพาะแพ็กเกจที่เซิร์ฟเวอร์ใช้ (ติดตั้งแล้วราว 400 MB ต่ำกว่าเพดาน 500 MB) ระบบค้นคืนด้วย TF-IDF ส่วนแพ็กเกจสำหรับเครื่องนักพัฒนาย้ายไป `requirements-dev.txt` |
| Vercel เขียนไฟล์ได้เฉพาะ `/tmp` | เมื่อมีตัวแปร `VERCEL` ระบบเก็บดัชนี FAISS และ `bookings.db` ไว้ที่ `/tmp/petcare` |
| บางโฮสต์ไม่เรียก lifespan ตอนเริ่ม | ระบบสร้าง state ในคำขอแรกแทน (มีเทสต์ `test_state_builds_on_first_request_without_lifespan`) |
| ไฟล์ที่ไม่ใช้ตอนรัน | `.vercelignore` ตัด tests, docs, scripts, `.env`, `*.db` |

## ขั้นตอน

1. เปิดโปรเจกต์บน Vercel → **Settings → Environment Variables** แล้วเพิ่ม (หรือวางทั้งชุดในช่อง Key)
   ```
   LLM_PROVIDER=openrouter
   OPENROUTER_MODEL=openai/gpt-4o-mini
   OPENROUTER_API_KEY=<key ของตัวเอง>
   ADMIN_TOKEN=<รหัสพนักงานที่ตั้งเอง>
   EMBEDDING_BACKEND=tfidf
   STORE_TIMEZONE=Asia/Bangkok
   ```
2. **Deployments** → จุดสามจุดของรายการล่าสุด → **Redeploy** (หรือ push โค้ดใหม่ Vercel deploy เอง)
3. เปิด `https://<โปรเจกต์>.vercel.app/health` ต้องเห็น `"status":"ok"` และ `"configured":true`

## ข้อจำกัดบน Vercel

- **คำขอจองคิวไม่ถาวร** `/tmp` อยู่ได้เฉพาะในเครื่องที่ยังทำงาน เมื่อ Vercel เริ่มเครื่องใหม่หรือแยกหลายเครื่อง คิวจะหายหรือเห็นไม่ตรงกัน ถ้าต้องใช้จริงต้องเปลี่ยนเป็นฐานข้อมูลภายนอก
- **ขนาดคำขอไม่เกิน 4.5 MB** รูปที่อัปโหลดต้องเล็กกว่านี้ (ระบบรับได้ถึง 5 MB เมื่อรันเอง)
- **เริ่มเครื่องเย็น** คำขอแรกหลังว่างนานใช้เวลาหลายวินาที เพราะต้องโหลดแพ็กเกจและสร้างดัชนี
- **ค่าใช้จ่าย OpenRouter** URL เป็นสาธารณะ ควรตั้ง credit limit ของ key
