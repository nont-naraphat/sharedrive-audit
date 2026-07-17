"""
server.py — FastAPI: serve frontend + API + scheduler

Endpoints:
  GET  /api/audit    -> audit.json
  GET  /api/export   -> audit.xlsx (download)
  GET  /api/status   -> สถานะ crawl (running/last_run/counts) + hasData
  POST /api/refresh  -> สั่ง crawl ทันที (background) — 409 ถ้ากำลังรันอยู่

Scheduler: รัน crawl อัตโนมัติตาม env CRAWL_CRON (default 02:00 ทุกคืน)

Config ผ่าน env:
  ADMIN_EMAIL, INTERNAL_DOMAINS, SA_FILE, OUTPUT_DIR, CRAWL_CRON, RUN_ON_START
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from audit_service import run_audit, STATE

BASE = os.path.dirname(__file__)

CFG = {
    "sa_file": os.getenv("SA_FILE", os.path.join(BASE, "sa.json")),
    "admin": os.getenv("ADMIN_EMAIL", ""),
    "domains": {d.strip().lower() for d in os.getenv(
        "INTERNAL_DOMAINS",
        "office21sun.com,21sunpassion.com,sunsusolution.com").split(",") if d.strip()},
    "out": os.getenv("OUTPUT_DIR", os.path.join(BASE, "..", "output")),
    "cron": os.getenv("CRAWL_CRON", "0 2 * * *"),
    "run_on_start": os.getenv("RUN_ON_START", "false").lower() == "true",
}

scheduler = BackgroundScheduler(timezone=os.getenv("TZ", "Asia/Bangkok"))


def _job():
    if not CFG["admin"]:
        print("[scheduler] ข้าม: ยังไม่ตั้ง ADMIN_EMAIL")
        return
    print("[scheduler] เริ่ม crawl ตามเวลา")
    try:
        run_audit(CFG["sa_file"], CFG["admin"], CFG["domains"], CFG["out"])
    except Exception as e:  # noqa
        print(f"[scheduler] error: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        scheduler.add_job(_job, CronTrigger.from_crontab(CFG["cron"]),
                          id="nightly-audit", replace_existing=True)
        scheduler.start()
        print(f"[i] scheduler on — cron '{CFG['cron']}' ({scheduler.timezone})")
    except Exception as e:  # noqa
        print(f"[!] scheduler ไม่ทำงาน: {e}")
    if CFG["run_on_start"] and not os.path.exists(os.path.join(CFG["out"], "audit.json")):
        import threading
        threading.Thread(target=_job, daemon=True).start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Shared Drive Audit", lifespan=lifespan)


@app.get("/api/status")
def status():
    data_path = os.path.join(CFG["out"], "audit.json")
    return {**STATE, "hasData": os.path.exists(data_path), "cron": CFG["cron"]}


@app.get("/api/audit")
def get_audit():
    path = os.path.join(CFG["out"], "audit.json")
    if not os.path.exists(path):
        raise HTTPException(404, "ยังไม่มีข้อมูล — กด Refresh เพื่อ crawl ครั้งแรก")
    return FileResponse(path, media_type="application/json")


@app.get("/api/export")
def export_xlsx():
    path = os.path.join(CFG["out"], "audit.xlsx")
    if not os.path.exists(path):
        raise HTTPException(404, "ยังไม่มีไฟล์ export — กด Refresh ก่อน")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="shared-drive-audit.xlsx",
    )


@app.post("/api/refresh")
def refresh(bg: BackgroundTasks):
    if not CFG["admin"]:
        raise HTTPException(400, "ยังไม่ตั้ง ADMIN_EMAIL ใน environment")
    if STATE["running"]:
        raise HTTPException(409, "กำลัง crawl อยู่แล้ว")
    bg.add_task(run_audit, CFG["sa_file"], CFG["admin"], CFG["domains"], CFG["out"])
    return {"status": "started"}


app.mount("/", StaticFiles(directory=os.path.join(BASE, "static"), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
