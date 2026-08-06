"""
groups_audit.py
Core library สำหรับ audit Google Workspace Groups ทั้งโดเมน
(Admin SDK Directory API + Groups Settings API) — ใช้ service account
ตัวเดียวกับ gdrive_audit แต่ต้องเพิ่ม scope ใน Domain-Wide Delegation

*** สำคัญ: ต้องเพิ่ม 3 scope ด้านล่างใน Admin Console ***
Security → Access and data control → API controls → Domain-wide delegation
ใส่ Client ID ของ SA แล้วแปะ scope (คั่นด้วย comma):
  https://www.googleapis.com/auth/admin.directory.group.readonly,
  https://www.googleapis.com/auth/admin.directory.group.member.readonly,
  https://www.googleapis.com/auth/apps.groups.settings
และ subject (ADMIN_EMAIL) ต้องเป็น super admin
"""

import time
import random

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.group.readonly",
    "https://www.googleapis.com/auth/admin.directory.group.member.readonly",
    "https://www.googleapis.com/auth/apps.groups.settings",
]

ROLE_ORDER = {"OWNER": 0, "MANAGER": 1, "MEMBER": 2}


def _creds(sa_file, subject):
    return service_account.Credentials.from_service_account_file(
        sa_file, scopes=SCOPES, subject=subject
    )


def _dir_svc(sa_file, subject):
    return build("admin", "directory_v1", credentials=_creds(sa_file, subject),
                 cache_discovery=False)


def _settings_svc(sa_file, subject):
    return build("groupssettings", "v1", credentials=_creds(sa_file, subject),
                 cache_discovery=False)


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


def is_external_email(email, internal_domains):
    dom = (email.split("@")[-1] if "@" in (email or "") else "").lower()
    return bool(dom) and dom not in internal_domains


def list_all_groups(dir_svc, customer="my_customer"):
    groups, page_token = [], None
    while True:
        resp = _exec(dir_svc.groups().list(
            customer=customer,
            maxResults=200,
            fields=("nextPageToken,groups(id,email,name,description,"
                    "directMembersCount,adminCreated,aliases)"),
            pageToken=page_token,
        ))
        groups.extend(resp.get("groups", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return groups


def list_group_members(dir_svc, group_key):
    members, page_token = [], None
    while True:
        resp = _exec(dir_svc.members().list(
            groupKey=group_key,
            maxResults=200,
            fields="nextPageToken,members(email,role,type,status)",
            pageToken=page_token,
        ))
        members.extend(resp.get("members", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return members


def get_group_settings(settings_svc, group_email):
    """ดึงการตั้งค่ากลุ่ม (whoCanJoin/allowExternalMembers ฯลฯ)
    คืน dict ว่างถ้าดึงไม่ได้ (ไม่ให้ทั้งงานล้มเพราะกลุ่มเดียว)"""
    try:
        s = _exec(settings_svc.groups().get(groupUniqueId=group_email, alt="json"))
    except HttpError:
        return {}
    return {
        "whoCanJoin": s.get("whoCanJoin"),
        "whoCanPostMessage": s.get("whoCanPostMessage"),
        "whoCanViewMembership": s.get("whoCanViewMembership"),
        "whoCanViewGroup": s.get("whoCanViewGroup"),
        "allowExternalMembers": (s.get("allowExternalMembers") == "true"),
        "archiveOnly": (s.get("archiveOnly") == "true"),
    }


def audit_all_groups(sa_file, admin_email, internal_domains):
    """ไล่ทุกกลุ่มทั้งโดเมน + สมาชิก + การตั้งค่า → โครงสร้างที่ front ใช้ได้เลย"""
    dir_svc = _dir_svc(sa_file, admin_email)
    set_svc = _settings_svc(sa_file, admin_email)

    groups = list_all_groups(dir_svc)
    total = len(groups)
    print(f"[i] พบ group {total} กลุ่ม", flush=True)

    out = []
    for i, g in enumerate(groups, 1):
        email = g.get("email")
        name = g.get("name") or email
        print(f"  -> [{i}/{total}] {name}", flush=True)

        try:
            raw_members = list_group_members(dir_svc, email)
        except HttpError as e:
            print(f"     [!] ดึง member ไม่ได้: {e}", flush=True)
            raw_members = []

        settings = get_group_settings(set_svc, email)

        members = []
        for m in raw_members:
            em = m.get("email", "")
            members.append({
                "email": em,
                "role": m.get("role", "MEMBER"),
                "type": m.get("type", "USER"),
                "status": m.get("status", "active"),
                "external": is_external_email(em, internal_domains),
            })
        members.sort(key=lambda x: (ROLE_ORDER.get(x["role"], 9), not x["external"]))
        ext_count = sum(1 for x in members if x["external"])

        out.append({
            "id": g.get("id"),
            "email": email,
            "name": name,
            "description": g.get("description", ""),
            "aliases": g.get("aliases", []) or [],
            "adminCreated": g.get("adminCreated"),
            "memberCount": len(members),
            "externalCount": ext_count,
            "allowExternalMembers": settings.get("allowExternalMembers", False),
            "archiveOnly": settings.get("archiveOnly", False),
            "settings": settings,
            "members": members,
        })

    # เรียงให้กลุ่มเสี่ยง (มี external / เปิดรับ external) ขึ้นก่อน แล้วตามจำนวนสมาชิก
    out.sort(key=lambda g: (
        1 if (g["externalCount"] > 0 or g["allowExternalMembers"]) else 0,
        g["memberCount"],
    ), reverse=True)
    return {"groups": out}
