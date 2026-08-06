"""
groups_service.py — orchestrator + xlsx export สำหรับ Group Mail Audit
mirror รูปแบบของ audit_service.py (STATE in-memory, thread-safe, เขียน .json + .xlsx)
แยกไฟล์ต่างหาก ไม่ไปแตะ audit_service.py เดิม
"""

import os
import json
import threading
import datetime as dt

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from groups_audit import audit_all_groups

# ---- สถานะ crawl groups (in-memory) ----
GROUPS_STATE = {
    "running": False,
    "started": None,
    "last_run": None,
    "last_error": None,
    "groups": 0,
    "members": 0,
    "external": 0,
}
_glock = threading.Lock()

HEADER_FILL = PatternFill("solid", fgColor="404040")
HEADER_FONT = Font(color="FFFFFF", bold=True)
EXT_FILL = PatternFill("solid", fgColor="FDECEC")


def _style(ws, ncols):
    for col in range(1, ncols + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(vertical="center")
    ws.freeze_panes = "A2"


def _autofit(ws, max_w=60):
    for col in ws.columns:
        letter = get_column_letter(col[0].column)
        width = max((len(str(c.value)) for c in col if c.value is not None), default=10)
        ws.column_dimensions[letter].width = min(width + 2, max_w)


def write_groups_xlsx(data, path):
    wb = Workbook()

    # ---- Sheet 1: Groups (ภาพรวมทุกกลุ่ม) ----
    ws = wb.active
    ws.title = "Groups"
    ws.append([
        "Group Name", "Email", "Members", "External Members", "Allow External",
        "Archive Only", "Who Can Join", "Who Can Post", "Who Can View Members",
        "Aliases", "Description",
    ])
    for g in data["groups"]:
        s = g.get("settings", {})
        ws.append([
            g["name"], g["email"], g["memberCount"], g["externalCount"],
            "YES" if g["allowExternalMembers"] else "no",
            "YES" if g.get("archiveOnly") else "no",
            s.get("whoCanJoin", ""), s.get("whoCanPostMessage", ""),
            s.get("whoCanViewMembership", ""),
            ", ".join(g.get("aliases", [])), g.get("description", ""),
        ])
    _style(ws, 11)
    _autofit(ws)

    # ---- Sheet 2: Members (ทุกสมาชิกทุกกลุ่ม, ไฮไลต์คนนอกองค์กร) ----
    wm = wb.create_sheet("Members")
    wm.append(["Group Name", "Group Email", "Member Email", "Role",
               "Type", "Status", "External", "Domain"])
    for g in data["groups"]:
        for m in g["members"]:
            dom = m["email"].split("@")[-1] if "@" in m["email"] else ""
            wm.append([g["name"], g["email"], m["email"], m["role"],
                       m["type"], m["status"],
                       "YES" if m["external"] else "no", dom])
            if m["external"]:
                r = wm.max_row
                for col in range(1, 9):
                    wm.cell(row=r, column=col).fill = EXT_FILL
    _style(wm, 8)
    _autofit(wm)

    wb.save(path)


def run_groups_audit(sa_file, admin_email, internal_domains, out_dir):
    """รัน crawl groups ทั้งโดเมน (thread-safe: กันรันซ้อน)"""
    with _glock:
        if GROUPS_STATE["running"]:
            return {"skipped": True, "reason": "already running"}
        GROUPS_STATE["running"] = True
        GROUPS_STATE["started"] = dt.datetime.now().isoformat(timespec="seconds")
        GROUPS_STATE["last_error"] = None

    try:
        data = audit_all_groups(sa_file, admin_email, internal_domains)
        data["generated"] = dt.datetime.now().isoformat(timespec="seconds")

        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "groups.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        write_groups_xlsx(data, os.path.join(out_dir, "groups.xlsx"))

        members = sum(g["memberCount"] for g in data["groups"])
        external = sum(g["externalCount"] for g in data["groups"])
        GROUPS_STATE.update(groups=len(data["groups"]), members=members,
                            external=external, last_run=data["generated"])
        print(f"[\u2713] groups audit เสร็จ ({len(data['groups'])} กลุ่ม / "
              f"{members} สมาชิก / external {external})", flush=True)
        return {"groups": len(data["groups"]), "members": members}
    except Exception as e:  # noqa
        GROUPS_STATE["last_error"] = str(e)
        raise
    finally:
        GROUPS_STATE["running"] = False
