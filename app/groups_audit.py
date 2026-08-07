"""
groups_audit.py
Core library สำหรับ audit Google Groups (group mail) ทั้ง tenant
รองรับหลาย workspace (คนละ admin / คนละโดเมน / คนละ sa.json ก็ได้)

ใช้ Admin SDK Directory API เป็นหลัก และ Groups Settings API (optional)
ถ้ายังไม่ได้ให้ scope settings ระบบจะข้ามส่วน settings ให้อัตโนมัติ
"""

import time
import random

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ---- scopes ----
# core: อ่านกลุ่ม + สมาชิก (จำเป็น)
DIR_SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.group.readonly",
    "https://www.googleapis.com/auth/admin.directory.group.member.readonly",
]
# optional: อ่านการตั้งค่ากลุ่ม (whoCanPost / allowExternalMembers ฯลฯ)
SET_SCOPES = ["https://www.googleapis.com/auth/apps.groups.settings"]

# แปล role/type เป็นไทยไว้โชว์
ROLE_TH = {"OWNER": "เจ้าของ", "MANAGER": "ผู้จัดการ", "MEMBER": "สมาชิก"}
TYPE_TH = {"USER": "ผู้ใช้", "GROUP": "กลุ่ม", "CUSTOMER": "ทั้งองค์กร",
           "EXTERNAL": "ภายนอก"}


def _dir_svc(sa_file, subject):
    creds = service_account.Credentials.from_service_account_file(
        sa_file, scopes=DIR_SCOPES, subject=subject)
    return build("admin", "directory_v1", credentials=creds,
                 cache_discovery=False)


def _settings_svc(sa_file, subject):
    """แยก service ต่างหาก — ถ้า scope settings ไม่ถูก authorize จะ error
    เฉพาะ service นี้ ไม่กระทบการดึงกลุ่ม/สมาชิก"""
    try:
        creds = service_account.Credentials.from_service_account_file(
            sa_file, scopes=SET_SCOPES, subject=subject)
        return build("groupssettings", "v1", credentials=creds,
                     cache_discovery=False)
    except Exception as e:  # noqa
        print(f"     [i] settings service off ({e})", flush=True)
        return None


def _exec(request, max_tries=6):
    for i in range(max_tries):
        try:
            return request.execute()
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            if status in (403, 429, 500, 502, 503) and i < max_tries - 1:
                time.sleep((2 ** i) + random.random())
                continue
            raise


def list_groups(dir_svc):
    """ดึงกลุ่มทั้ง tenant (customer=my_customer = ครบทุกโดเมนใน tenant นั้น)"""
    groups = []
    page_token = None
    while True:
        resp = _exec(dir_svc.groups().list(
            customer="my_customer",
            maxResults=200,
            orderBy="email",
            pageToken=page_token,
        ))
        groups.extend(resp.get("groups", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return groups


def list_members(dir_svc, group_key):
    """สมาชิกตรงของกลุ่ม (ไม่ขยาย nested group)"""
    members = []
    page_token = None
    while True:
        resp = _exec(dir_svc.members().list(
            groupKey=group_key,
            maxResults=200,
            pageToken=page_token,
        ))
        members.extend(resp.get("members", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return members


# เก็บเฉพาะ field ที่มีประโยชน์ต่อ audit
SETTINGS_KEYS = [
    "whoCanJoin", "whoCanPostMessage", "whoCanViewGroup",
    "whoCanViewMembership", "allowExternalMembers",
    "membersCanPostAsTheGroup", "isArchived", "archiveOnly",
    "messageModerationLevel",
]


def get_group_settings(set_svc, email):
    if not set_svc:
        return {}
    try:
        s = _exec(set_svc.groups().get(groupUniqueId=email))
    except Exception as e:  # noqa
        print(f"       [!] settings {email}: {e}", flush=True)
        return {}
    return {k: s.get(k) for k in SETTINGS_KEYS if k in s}


def _is_external_member(m, internal_domains):
    if m.get("type") == "EXTERNAL":
        return True
    email = (m.get("email") or "").lower()
    dom = email.split("@")[-1] if "@" in email else ""
    return bool(dom) and dom not in internal_domains


def audit_workspace(ws, out_progress=None):
    """audit 1 workspace -> dict สรุป + list กลุ่มพร้อมสมาชิก
    ws = {"id","name","sa_file","admin","domains"(set)}
    """
    sa_file, admin = ws["sa_file"], ws["admin"]
    internal = ws["domains"]

    dir_svc = _dir_svc(sa_file, admin)
    set_svc = _settings_svc(sa_file, admin)

    groups = list_groups(dir_svc)
    total = len(groups)
    print(f"[i] [{ws['id']}] พบกลุ่ม {total} กลุ่ม", flush=True)

    out_groups = []
    ext_group_count = 0
    for i, g in enumerate(groups, 1):
        gemail = g.get("email")
        gname = g.get("name") or gemail
        print(f"  -> [{ws['id']} {i}/{total}] {gemail}", flush=True)
        try:
            raw = list_members(dir_svc, gemail)
        except HttpError as e:
            print(f"     [!] ข้าม members ({e})", flush=True)
            raw = []

        members = []
        n_ext = 0
        role_cnt = {"OWNER": 0, "MANAGER": 0, "MEMBER": 0}
        for m in raw:
            ext = _is_external_member(m, internal)
            if ext:
                n_ext += 1
            role = m.get("role", "MEMBER")
            role_cnt[role] = role_cnt.get(role, 0) + 1
            members.append({
                "email": m.get("email", ""),
                "role": role,
                "roleTh": ROLE_TH.get(role, role),
                "type": m.get("type", ""),
                "typeTh": TYPE_TH.get(m.get("type", ""), m.get("type", "")),
                "status": m.get("status", ""),
                "external": ext,
            })
        # เรียง: owner -> manager -> member, แล้วตามอีเมล
        order = {"OWNER": 0, "MANAGER": 1, "MEMBER": 2}
        members.sort(key=lambda x: (order.get(x["role"], 9), x["email"]))

        settings = get_group_settings(set_svc, gemail)
        allow_ext = settings.get("allowExternalMembers") in ("true", True)
        if n_ext or allow_ext:
            ext_group_count += 1

        out_groups.append({
            "email": gemail,
            "name": gname,
            "description": g.get("description", ""),
            "directMembersCount": int(g.get("directMembersCount") or 0),
            "aliases": g.get("aliases", []) or [],
            "adminCreated": g.get("adminCreated"),
            "memberCount": len(members),
            "externalCount": n_ext,
            "roleCount": role_cnt,
            "allowExternalMembers": allow_ext,
            "settings": settings,
            "members": members,
        })
        if out_progress:
            out_progress(i, total)

    # เรียงกลุ่ม: ที่มี external ขึ้นก่อน แล้วตามสมาชิกมาก->น้อย
    out_groups.sort(key=lambda x: (-(x["externalCount"] > 0 or x["allowExternalMembers"]),
                                   -x["memberCount"], x["email"]))

    return {
        "id": ws["id"],
        "name": ws["name"],
        "admin": admin,
        "domains": sorted(internal),
        "groupCount": len(out_groups),
        "externalGroupCount": ext_group_count,
        "totalMembers": sum(g["memberCount"] for g in out_groups),
        "groups": out_groups,
    }
