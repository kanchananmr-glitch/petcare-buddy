# วิธีติดตั้งและรัน PetCare Buddy บน Windows

ใช้ได้กับ Windows 10 และ 11 ทุกคำสั่งพิมพ์ใน **Command Prompt** (ค้นหาคำว่า `cmd` ในเมนู Start) พิมพ์ทีละบรรทัด กด Enter แล้วรอให้เสร็จก่อนพิมพ์บรรทัดถัดไป

## 1. ติดตั้ง Python (ทำครั้งเดียว)

1. ดาวน์โหลด Python 3.12 จาก https://www.python.org/downloads/windows/ เลือก "Windows installer (64-bit)"
2. ตอนติดตั้ง **ติ๊กช่อง "Add python.exe to PATH"** ที่ด้านล่างของหน้าต่างแรก แล้วกด "Install Now"
3. ปิดแล้วเปิด Command Prompt ใหม่ ตรวจว่าติดตั้งสำเร็จ

```
python --version
```

ต้องขึ้นเลขเวอร์ชัน เช่น `Python 3.12.x` ถ้าขึ้นว่าไม่รู้จักคำสั่ง หรือเปิด Microsoft Store ขึ้นมา แปลว่ายังไม่ได้ติ๊ก "Add python.exe to PATH" ให้ติดตั้ง Python ใหม่แล้วติ๊กช่องนี้

## 2. แตกไฟล์ zip

1. คลิกขวาที่ไฟล์ zip ในโฟลเดอร์ Downloads แล้วเลือก **Extract All…** จากนั้นกด Extract
2. Windows จะสร้างโฟลเดอร์ซ้อนกันสองชั้น เช่น `Downloads\PetCare_Buddy_v3\PetCare_Buddy` โฟลเดอร์ที่ต้องใช้คือชั้นใน ซึ่งมีโฟลเดอร์ `app`, `data` และไฟล์ `requirements.txt`

## 3. เข้าโฟลเดอร์โครงงาน

วิธีที่ง่ายที่สุด: เปิดโฟลเดอร์ `PetCare_Buddy` ชั้นในใน File Explorer คลิกที่ช่องที่อยู่ (address bar) ด้านบน พิมพ์ `cmd` แล้วกด Enter จะได้ Command Prompt ที่อยู่ในโฟลเดอร์นั้นทันที

ตรวจว่าอยู่ถูกที่

```
dir requirements.txt
```

ต้องพบไฟล์ ถ้าขึ้นว่า File Not Found แปลว่ายังอยู่ผิดโฟลเดอร์

## 4. สร้างสภาพแวดล้อม Python และติดตั้ง (ทำครั้งเดียว)

```
python -m venv .venv
.venv\Scripts\activate
```

หน้าบรรทัดคำสั่งต้องมี `(.venv)` แล้วติดตั้งต่อ

```
python -m pip install --upgrade pip
pip install -r requirements.txt
```

ขั้นนี้ใช้เวลาประมาณ 5–15 นาที เพราะต้องดาวน์โหลด PyTorch

> ถ้าเคยติดตั้งเวอร์ชันก่อนหน้าแล้ว ให้รัน `pip install -r requirements.txt` อีกครั้ง เพื่อติดตั้งแพ็กเกจ `tzdata` (ข้อมูลเขตเวลาที่ Windows ไม่มีให้ ถ้าขาดตัวนี้ การส่งคำขอจองคิวจะ error)

## 5. ตั้งค่า API key และรหัสพนักงาน (ทำครั้งเดียว)

```
copy .env.example .env
notepad .env
```

ใน Notepad แก้สองบรรทัดนี้ (ไม่มีช่องว่างและไม่ต้องใส่เครื่องหมายคำพูด)

```
OPENROUTER_API_KEY=sk-or-v1-...
ADMIN_TOKEN=ตั้งรหัสเอง เช่น petcare2569
```

กด `Ctrl+S` เพื่อบันทึก แล้วปิด Notepad

ถ้ายังไม่มี key ให้สมัครและสร้าง key เองที่ https://openrouter.ai/keys อย่าส่ง key ผ่านแชทกลุ่มหรือใส่ในไฟล์ที่แชร์ต่อ

## 6. เปิดใช้งาน

```
uvicorn app.main:app --port 8000
```

เมื่อขึ้นข้อความ `Application startup complete` ให้เปิดเบราว์เซอร์ไปที่

- http://localhost:8000 หน้าแชทสำหรับลูกค้า
- http://localhost:8000/admin หน้าพนักงาน ใส่รหัสจาก `ADMIN_TOKEN`
- http://localhost:8000/docs เอกสาร API

การเปิดครั้งแรกจะช้ากว่าปกติ 1–3 นาที เพราะต้องดาวน์โหลดโมเดลภาษา ถ้า Windows Firewall ถามสิทธิ์ ให้เลือก Allow เฉพาะ Private networks

หยุดเซิร์ฟเวอร์: กลับมาที่ Command Prompt แล้วกด `Ctrl+C`

## 7. รันการทดสอบทั้งหมด (ไม่บังคับ)

ปิดเซิร์ฟเวอร์จากขั้นที่ 6 ก่อน แล้วรัน

```
python scripts\run_all.py
```

ผลอยู่ที่ `docs\test_results.md` ถ้าต้องการให้ทดสอบภาพ B01 และ B02 ให้ใส่รูปที่ `tests\images\dog.jpg` และ `tests\images\cat.jpg` ก่อนรัน

## ครั้งต่อไป

เปิด Command Prompt ในโฟลเดอร์ `PetCare_Buddy` (วิธีเดียวกับขั้นที่ 3) แล้วพิมพ์

```
.venv\Scripts\activate
uvicorn app.main:app --port 8000
```

## ปัญหาที่พบบ่อย

| อาการ | วิธีแก้ |
|---|---|
| `'python' is not recognized` หรือเปิด Microsoft Store | ติดตั้ง Python ใหม่และติ๊ก "Add python.exe to PATH" |
| `No such file or directory: 'requirements.txt'` | อยู่ผิดโฟลเดอร์ ให้เข้าโฟลเดอร์ `PetCare_Buddy` ชั้นใน |
| ใช้ PowerShell แล้วขึ้น `running scripts is disabled` ตอน activate | ใช้ Command Prompt (`cmd`) แทน PowerShell |
| `'uvicorn' is not recognized` | ยังไม่ได้ activate ให้รัน `.venv\Scripts\activate` ก่อน |
| `error: Microsoft Visual C++ 14.0 is required` ตอน pip install | ใช้ Python 3.12 64-bit ตามขั้นที่ 1 แล้วสร้าง `.venv` ใหม่ |
| `[Errno 10048]` หรือ address already in use | มีเซิร์ฟเวอร์เปิดอยู่แล้ว ปิดหน้าต่างเดิม หรือใช้ `--port 8001` |
| หน้าเว็บขึ้น "ยังไม่ได้ตั้งค่า LLM" | ตรวจบรรทัด `OPENROUTER_API_KEY=` ใน `.env` แล้วเปิดเซิร์ฟเวอร์ใหม่ |
| หน้า admin ขึ้น "ยังไม่ได้ตั้งค่า ADMIN_TOKEN" | ใส่ `ADMIN_TOKEN=` ใน `.env` แล้วเปิดเซิร์ฟเวอร์ใหม่ |
