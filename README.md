# Shared Drive Audit — 21 Sunpassion

Web app: ดึงทุก Google Shared Drive ในโดเมน แสดงเป็น **tree แบบ explorer**
(จำนวนไฟล์/โฟลเดอร์ · permission รายคน · วันแก้ล่าสุด · คนแก้ล่าสุด · ขนาด)
พร้อม **ปุ่ม Refresh** สั่ง crawl เอง + **auto crawl ทุกคืน** และ **Export Excel**

Frontend (vanilla JS) → Backend API (FastAPI) ในคอนเทนเนอร์เดียว

```
sharedrive-audit/
├── app/
│   ├── gdrive_audit.py    # auth + enumerate drive + walk + permission
│   ├── audit_service.py   # run_audit() + write_xlsx() + STATE (ใช้ร่วม CLI/API)
│   ├── server.py          # FastAPI: /api/audit /export /status /refresh + scheduler
│   ├── crawl.py           # CLI รันมือ (เรียก run_audit ตัวเดียวกับ API)
│   └── static/index.html  # หน้า tree + refresh + detail
├── Dockerfile
├── compose.yaml           # Portainer stack
├── requirements.txt
├── .gitignore             # กัน sa.json / data หลุดขึ้น git
├── .env.example
└── output/                # (dev เท่านั้น; prod ใช้ volume /data)
```

## API
| Method | Path | ทำอะไร |
|---|---|---|
| GET | `/api/audit` | คืน audit.json (tree + members) |
| GET | `/api/export` | ดาวน์โหลด audit.xlsx |
| GET | `/api/status` | สถานะ crawl (running/last_run/counts/cron) |
| POST | `/api/refresh` | สั่ง crawl ทันที (background) — 409 ถ้ากำลังรัน |

## 1) เตรียม Service Account + Domain-Wide Delegation (ครั้งเดียว)
1. Google Cloud Console → สร้าง Service Account → Key (JSON) = `sa.json`
2. เปิด **Google Drive API** ใน project
3. เปิด **Domain-Wide Delegation** ที่ SA (จด Client ID)
4. Admin Console → Security → API controls → Domain-wide delegation → Add → Client ID + scope:
   ```
   https://www.googleapis.com/auth/drive
   ```
5. `ADMIN_EMAIL` ต้องเป็นบัญชีที่มีสิทธิ์ **Manage shared drives**

> ไฟล์ใน drive ที่ admin ไม่ได้เป็น member: แอป impersonate organizer ของ drive นั้นให้เอง (แนวเดียวกับ GAM)

## 2) Deploy บน Portainer (Git method)
1. push repo นี้ขึ้น GitHub (`nont-naraphat/sharedrive-audit`) — `sa.json` ไม่ขึ้นเพราะติด `.gitignore`
2. บน Synology วางไฟล์ key ไว้ที่:
   ```
   /volume1/docker/sharedrive-audit/secrets/sa.json
   /volume1/docker/sharedrive-audit/data/            (โฟลเดอร์ว่าง ให้ container เขียนผล)
   ```
3. Portainer → Stacks → Add stack → **Repository** → ใส่ URL repo, compose path `compose.yaml`
4. แก้ค่าใน `compose.yaml` ตามจริง (`ADMIN_EMAIL`, port ซ้ายมือ ถ้าชนกับ service อื่น)
5. Deploy → เปิด `http://<synology-ip>:8091`
   - ครั้งแรก `RUN_ON_START=true` จะ crawl ให้อัตโนมัติ (หรือกดปุ่ม Refresh)
   - หลังจากนั้น auto crawl ทุกคืน 02:00 (แก้ `CRAWL_CRON`)

### ⚠️ DNS ตอน build บน Synology
Portainer build ต้องดึงจาก github + pypi ถ้า resolve ไม่ได้ (เคยเจอ `192.168.0.1:53`):
- **วิธี A**: แก้ DNS ของ Docker daemon บน Synology → `/etc/docker/daemon.json` เพิ่ม
  `{"dns": ["1.1.1.1","8.8.8.8"]}` แล้ว restart Docker
- **วิธี B**: build image ที่เครื่องมีเน็ตแล้ว push ขึ้น registry → uncomment บรรทัด `image:` ใน compose แล้วลบ `build: .`

(`dns:` ใน compose แก้เฉพาะตอน container วิ่ง ไม่ครอบคลุมตอน build)

## 3) รันมือ (dev / ไม่ผ่าน Docker)
```bash
pip install -r requirements.txt
cd app
ADMIN_EMAIL=admin@office21sun.com python crawl.py --sa ../sa.json --out ../output
python server.py     # http://localhost:8000
```

## Excel (3 sheet)
- **Summary** — ต่อ drive: ไฟล์/โฟลเดอร์/ขนาดรวม/จำนวน member/วันสร้าง
- **Files** — ทุกไฟล์+โฟลเดอร์ (path/type/size/created/modified/last modified by/link)
- **Members** — permission รายคนต่อ drive (organizer/fileOrganizer/writer/commenter/reader)

## ข้อจำกัด (ธรรมชาติ Shared Drive)
- **ไม่มี owner รายไฟล์** — ใช้ *Last Modified By* + *member ระดับ drive* แทน
- **"ใช้ล่าสุด" = วันแก้ล่าสุด (modifiedTime)** — ถ้าอยากได้ "เปิดดูล่าสุดโดยใครก็ได้" ต้องต่อ Drive Activity API เพิ่ม
- **ไม่มี auth** (ตามที่เลือก internal-trusted) — อยู่หลัง FortiGate; ถ้าจะเปิดออกนอกวงควรเพิ่ม auth ก่อน
