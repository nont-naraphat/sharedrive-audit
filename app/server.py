"""
server.py — FastAPI: serve frontend + API + scheduler
"""

import os
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from audit_service import (run_audit, STATE, export_permissions_csv,
                           PERM_STATE)
from gdrive_audit import (_svc_cached, list_item_permissions,
                          pick_crawl_subject, is_external, is_direct, ROLE_TH)

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


_svc = _svc_cached(CFG["sa_file"])
_members_cache = {"mtime": 0, "map": {}}


def _members_map():
    """โหลด members ต่อ drive จาก audit.json (cache ตาม mtime)"""
    path = os.path.join(CFG["out"], "audit.json")
    if not os.path.exists(path):
        return {}
    m = os.path.getmtime(path)
    if m != _members_cache["mtime"]:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        _members_cache["map"] = data.get("members", {})
        _members_cache["mtime"] = m
    return _members_cache["map"]


@app.get("/api/permissions")
def item_permissions(item: str, drive: str):
    """ดึง permission ของ item เดียวแบบสด (ใช้ตอนคลิกในหน้าเว็บ)"""
    if not CFG["admin"]:
        raise HTTPException(400, "ยังไม่ตั้ง ADMIN_EMAIL")
    members = _members_map().get(drive, [])
    subject = pick_crawl_subject(members, CFG["domains"], CFG["admin"])
    try:
        svc = _svc(subject)
        perms = list_item_permissions(svc, item)
    except Exception as e:  # noqa
        raise HTTPException(502, f"ดึง permission ไม่ได้: {e}")
    out = []
    for p in perms:
        out.append({
            "name": p.get("displayName") or p.get("emailAddress") or p.get("type"),
            "email": p.get("emailAddress"),
            "memberType": p.get("type"),
            "role": p.get("role"),
            "roleTh": ROLE_TH.get(p.get("role", ""), p.get("role", "")),
            "external": is_external(p, CFG["domains"]),
            "inherited": not is_direct(p),
        })
    order = {"owner": 0, "organizer": 1, "fileOrganizer": 2, "writer": 3,
             "commenter": 4, "reader": 5}
    out.sort(key=lambda x: order.get(x["role"], 9))
    return {"permissions": out}


@app.get("/api/perm-status")
def perm_status():
    csv_path = os.path.join(CFG["out"], "permissions.csv")
    return {**PERM_STATE, "hasFile": os.path.exists(csv_path)}


@app.post("/api/export-permissions")
def start_perm_export(bg: BackgroundTasks):
    if not CFG["admin"]:
        raise HTTPException(400, "ยังไม่ตั้ง ADMIN_EMAIL")
    if PERM_STATE["running"]:
        raise HTTPException(409, "กำลัง export permission อยู่แล้ว")
    bg.add_task(export_permissions_csv, CFG["sa_file"], CFG["admin"],
                CFG["domains"], CFG["out"])
    return {"status": "started"}


@app.get("/api/export-permissions/download")
def download_perm_csv():
    path = os.path.join(CFG["out"], "permissions.csv")
    if not os.path.exists(path):
        raise HTTPException(404, "ยังไม่มีไฟล์ — กด Export Permissions ก่อน")
    return FileResponse(path, media_type="text/csv",
                        filename="shared-drive-permissions.csv")


app.mount("/", StaticFiles(directory=os.path.join(BASE, "static"), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
