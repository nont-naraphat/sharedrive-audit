# SETUP — Deploy Shared Drive Audit บน Portainer (ทุกขั้นตอน + ลิงก์)

ทำตามลำดับ 6 เฟส ใช้เวลารวม ~30–45 นาที (ไม่รวมเวลารอ DWD propagate)

---

## เฟส 0 — เตรียมสิทธิ์
- ต้องมีบัญชี **Super Admin** ของ Google Workspace (office21sun.com)
- บัญชีที่จะใช้เป็น `ADMIN_EMAIL` ต้องมีสิทธิ์ **Manage shared drives**
- เข้า Synology / Portainer ได้ (Docker ทำงานอยู่)

---

## เฟส 1 — Google Cloud (สร้าง Service Account + Key)

### 1.1 สร้าง Project
เปิด → https://console.cloud.google.com/projectcreate
- Project name: `sharedrive-audit` → Create

### 1.2 เปิด Google Drive API
เปิด (เลือก project ที่เพิ่งสร้างมุมบน) → https://console.cloud.google.com/apis/library/drive.googleapis.com
- กด **Enable**

### 1.3 สร้าง Service Account
เปิด → https://console.cloud.google.com/iam-admin/serviceaccounts
- **+ Create service account**
- Name: `sharedrive-audit-sa` → Create and continue → (ข้าม role ได้) → Done

### 1.4 สร้าง + ดาวน์โหลด Key (JSON)
- คลิกที่ service account ที่เพิ่งสร้าง → แท็บ **Keys** → **Add key → Create new key → JSON → Create**
- ไฟล์ `.json` จะดาวน์โหลดมา → **เปลี่ยนชื่อเป็น `sa.json`** เก็บให้ดี (นี่คือกุญแจ ห้ามหลุด)

> ⚠️ ถ้าขึ้น error **"Key creation is not allowed / disabled"** = องค์กรเปิด org policy
> `iam.disableServiceAccountKeyCreation` อยู่ ต้องไปปิดชั่วคราวก่อน:
> เปิด → https://console.cloud.google.com/iam-admin/orgpolicies
> (ต้องมี role **Organization Policy Administrator**) → ค้น `disableServiceAccountKeyCreation`
> → Manage policy → Override / Off → Save → รอ ~1 ชม. แล้วกลับมาทำ 1.4 ใหม่
> (สร้าง key เสร็จแล้วแนะนำเปิด policy กลับ)

### 1.5 คัดลอก Client ID (ตัวเลข) ไว้ใช้เฟส 2
- ที่หน้า service account → แท็บ **Details** → คัดลอกค่า **Unique ID / OAuth 2 Client ID** (ตัวเลขยาวๆ)

---

## เฟส 2 — Admin Console (Domain-Wide Delegation)

เปิด → https://admin.google.com/ac/owl/domainwidedelegation
(เส้นทางเมนู: Security → Access and data control → API controls → Domain-wide delegation → **Manage Domain-Wide Delegation**)

- **Add new**
- Client ID: วางค่าตัวเลขจากขั้น 1.5
- OAuth scopes: วางบรรทัดนี้
  ```
  https://www.googleapis.com/auth/drive
  ```
- **Authorize**

> ⚠️ ถ้าองค์กรเปิด **Multi-party approval** (ค่า default ตั้งแต่ ส.ค. 2024 บางแพ็กเกจ)
> การ authorize อาจต้องให้ **Super Admin คนที่สอง** กดอนุมัติก่อนจึงมีผล
> การเปลี่ยนแปลงใช้เวลาแพร่กระจายได้ถึง ~24 ชม. (ปกติเร็วกว่านั้น)

---

## เฟส 3 — GitHub (push repo)

```bash
cd sharedrive-audit
git init
git add .
git commit -m "Shared Drive Audit app"
git branch -M main
git remote add origin https://github.com/nont-naraphat/sharedrive-audit.git
git push -u origin main
```

> `sa.json` จะ **ไม่ถูก push** เพราะติด `.gitignore` แล้ว (ตั้งใจ — กุญแจต้องไปวางบน Synology เอง)
> repo เป็น private ได้ ไม่มีปัญหา ถ้า Portainer เข้าถึงผ่าน token/SSH

---

## เฟส 4 — Synology (วางไฟล์)

ผ่าน File Station / SSH สร้างโครงและวาง `sa.json`:
```
/volume1/docker/sharedrive-audit/secrets/sa.json     ← ไฟล์ key จากเฟส 1
/volume1/docker/sharedrive-audit/data/               ← โฟลเดอร์ว่าง (ให้แอปเขียนผล)
```
> ใช้ path `/volume1/docker/...` เท่านั้น (อย่าใช้ `/volume1/web` เพราะเป็นของ Web Station จะ 403/404)

---

## เฟส 5 — Portainer (deploy stack)

1. Portainer → **Stacks → + Add stack**
2. Name: `sharedrive-audit`
3. Build method: **Repository**
   - Repository URL: `https://github.com/nont-naraphat/sharedrive-audit`
   - Repository reference: `refs/heads/main`
   - Compose path: `compose.yaml`
   - (repo private → เปิด **Authentication** ใส่ username + Personal Access Token)
4. ตรวจ/แก้ค่าใน `compose.yaml` ก่อน deploy:
   - `ADMIN_EMAIL` = super admin จริง
   - port ซ้ายมือ = `8091` (ตั้งไว้แล้ว ไม่ชน)
5. **Deploy the stack**
6. เปิด → `http://<synology-ip>:8091`

### ถ้า build ล้มเพราะ DNS (เคยเจอ resolve github/pypi ไม่ได้ผ่าน 192.168.0.1:53)
เลือกทางใดทางหนึ่ง:

**ทาง A — แก้ DNS ของ Docker daemon** (SSH เข้า Synology)
```bash
sudo vi /etc/docker/daemon.json
```
เพิ่ม:
```json
{ "dns": ["1.1.1.1", "8.8.8.8"] }
```
```bash
sudo synosystemctl restart pkgctl-ContainerManager   # หรือ restart Container Manager จาก DSM
```
แล้ว redeploy stack

**ทาง B — build image ที่เครื่องมีเน็ตแล้ว push**
```bash
docker build -t ghcr.io/nont-naraphat/sharedrive-audit:latest .
docker push ghcr.io/nont-naraphat/sharedrive-audit:latest
```
แล้วใน `compose.yaml` คอมเมนต์ `build: .` ออก + uncomment บรรทัด `image:` → redeploy

---

## เฟส 6 — รันครั้งแรก + ตรวจสอบ

- ครั้งแรก `RUN_ON_START=true` แอปจะ crawl อัตโนมัติ (หรือกดปุ่ม **Refresh** บนหน้าเว็บ)
- ดู log ใน Portainer → container `sharedrive-audit` → Logs ควรเห็น:
  ```
  [i] scheduler on — cron '0 2 * * *' (Asia/Bangkok)
  [i] พบ shared drive N ตัว
  ```
- เสร็จแล้ว tree จะขึ้น กด drive ดู permission, กด **Export** โหลด Excel
- หลังจากนี้ auto crawl ทุกคืน 02:00

### เช็กด่วนถ้า tree ว่าง / error
| อาการ | สาเหตุ | แก้ |
|---|---|---|
| `403 / insufficient permission` | DWD ยังไม่ propagate หรือ scope ผิด | รอ / ตรวจ scope เฟส 2 |
| `admin ... not delegated` | `ADMIN_EMAIL` ไม่ใช่ super admin | เปลี่ยนเป็น super admin |
| drive ขึ้นแต่ไม่มีไฟล์ข้างใน | ไม่มี organizer ภายในให้ impersonate | ตรวจ member ของ drive นั้น |
| หา `sa.json` ไม่เจอ | path mount ผิด | ตรวจเฟส 4 |

---

## ลิงก์อ้างอิง
- Google Cloud Console — https://console.cloud.google.com
- Domain-wide delegation (Admin) — https://admin.google.com/ac/owl/domainwidedelegation
- Google DWD docs — https://support.google.com/a/answer/162106
- Portainer docs — https://docs.portainer.io
