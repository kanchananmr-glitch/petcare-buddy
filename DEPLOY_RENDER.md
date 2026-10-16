# Deploy PetCare Buddy ขึ้น Cloud (Docker + Render)

ทำตามขั้นตอนของสัปดาห์ที่ 10 (FastAPI · OpenRouter · Docker · Render) ใช้แผนฟรีของ Render

| สิ่งที่ใช้ | ไฟล์ในโปรเจกต์ |
|---|---|
| Docker image | `Dockerfile`, `.dockerignore`, `requirements-deploy.txt` |
| ตั้งค่า Render อัตโนมัติ (Blueprint) | `render.yaml` |

ทดสอบก่อน deploy (16 ต.ค. 2569): ติดตั้งเฉพาะ `requirements-deploy.txt` บน Python 3.12 แล้วรันด้วยคำสั่งเดียวกับใน Dockerfile ได้ผล `/health` ตอบ ok หน้าเว็บทั้ง 4 หน้าและ API ตอบ 200 เริ่มระบบใช้เวลาราว 1 วินาที ใช้หน่วยความจำราว 160 MB (แผนฟรีให้ 512 MB)

## ขั้นที่ 1 สร้าง GitHub repo เปล่า (ทำเอง ครั้งเดียว)

1. เข้า https://github.com/new ด้วยบัญชีของตัวเอง
2. Repository name: `petcare-buddy`
3. เลือก **Private** ได้ (Render ดึง private repo ได้)
4. **ไม่ต้องติ๊ก** Add a README, .gitignore หรือ license (ต้องเป็น repo เปล่า)
5. กด **Create repository** แล้วแจ้งชื่อ repo ให้ Claude push โค้ดให้ หรือ push เองด้วยคำสั่ง

```bash
cd PetCare_Buddy
git init -b main
git add .
git commit -m "PetCare Buddy final project"
git remote add origin https://github.com/<ชื่อบัญชี>/petcare-buddy.git
git push -u origin main
```

ไฟล์ `.env` อยู่ใน `.gitignore` จึงไม่ถูก push ห้ามใส่ API key ลงในไฟล์ใดที่ push ขึ้น GitHub

## ขั้นที่ 2 สร้างบริการบน Render

1. เข้า https://dashboard.render.com แล้วสมัคร/เข้าสู่ระบบด้วย **GitHub**
2. ถ้า Render ยังไม่เห็น repo: ไปที่ **Account Settings → GitHub → Configure** แล้วอนุญาต repo `petcare-buddy`
3. กด **New → Blueprint** แล้วเลือก repo `petcare-buddy`
   Render อ่าน `render.yaml` แล้วแสดงบริการชื่อ `petcare-buddy` (Docker, Free, Singapore)
4. ช่อง **OPENROUTER_API_KEY**: วาง OpenRouter key ของตัวเอง (ช่องนี้ Render ถามเพราะตั้ง `sync: false` ไว้)
5. กด **Apply** แล้วรอ build ประมาณ 3–6 นาที ดู log ในแท็บ **Logs** จนเห็น `PetCare Buddy ready: 40 chunks`
6. URL ของระบบอยู่ด้านบนของหน้าบริการ รูปแบบ `https://petcare-buddy-xxxx.onrender.com`

วิธีสำรองถ้าไม่ใช้ Blueprint: **New → Web Service** → เลือก repo → Language: **Docker** → Instance type: **Free** → เพิ่ม Environment Variables ตามตารางด้านล่างเอง → **Deploy Web Service**

| ตัวแปร | ค่า |
|---|---|
| `LLM_PROVIDER` | `openrouter` |
| `OPENROUTER_MODEL` | `openai/gpt-4o-mini` |
| `OPENROUTER_API_KEY` | key ของตัวเอง (เป็นความลับ) |
| `ADMIN_TOKEN` | รหัสพนักงานที่ตั้งเอง (Blueprint สร้างให้อัตโนมัติ) |
| `EMBEDDING_BACKEND` | `tfidf` |
| `STORE_TIMEZONE` | `Asia/Bangkok` |

## ขั้นที่ 3 ตรวจว่าใช้งานได้

1. เปิด `https://<URL>/health` ต้องเห็น `"status":"ok"` และ `"llm":{"provider":"openrouter",...,"configured":true}`
2. เปิด `https://<URL>/` แล้วถาม "ร้านเปิดกี่โมง" และ "พรุ่งนี้มีคิวว่างไหม"
3. รหัสพนักงาน: หน้าบริการบน Render → **Environment** → ค่า `ADMIN_TOKEN` (กดรูปตาเพื่อดู) ใช้เข้า `https://<URL>/admin`
4. รันชุดทดสอบกับเว็บจริงจากเครื่องตัวเอง

```bash
python scripts/run_eval.py --base-url https://<URL>
python scripts/rule_spot_check.py --base-url https://<URL> --all
```

## ข้อจำกัดของแผนฟรี (ตรงกับที่สไลด์สัปดาห์ที่ 10 เตือนไว้)

- **เซิร์ฟเวอร์หลับ** เมื่อไม่มีคนใช้ 15 นาที คำขอแรกหลังจากนั้นรอราว 30–60 วินาที ก่อนอัดคลิปหรือนำเสนอให้เปิด `/health` ทิ้งไว้ก่อน 1 นาที
- **ไฟล์ระบบเป็นแบบชั่วคราว** คำขอจองคิวใน SQLite หายเมื่อ deploy ใหม่หรือเซิร์ฟเวอร์เริ่มใหม่ ถ้าต้องเก็บถาวรต้องใช้ Render Disk (แผนเสียเงิน) หรือฐานข้อมูลภายนอก เช่น Render PostgreSQL
- **การค้นคืนใช้ TF-IDF** แทน Sentence Transformers เพราะ torch ใช้หน่วยความจำเกิน 512 MB ผลทดสอบในรายงานหัวข้อ 18–20 รันด้วย Sentence Transformers บน MacBook ให้รันชุดทดสอบกับเว็บจริงอีกครั้งเพื่อดูผลของ TF-IDF
- **ค่าใช้จ่าย OpenRouter** URL เป็นสาธารณะ ใครเข้าถึงก็ใช้เครดิตได้ ควรตั้ง credit limit ของ key ใน https://openrouter.ai/settings/keys ไว้ต่ำ ๆ และลบ key หลังส่งงาน

## อัปเดตระบบ

แก้โค้ดแล้ว `git push` Render build และ deploy ใหม่ให้อัตโนมัติ (`autoDeploy: true`)
