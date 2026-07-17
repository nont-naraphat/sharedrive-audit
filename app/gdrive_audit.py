"""
gdrive_audit.py
Core library สำหรับ audit Google Shared Drives ทั้งโดเมน

หลักการ:
- ใช้ Service Account + Domain-Wide Delegation (DWD)
- impersonate super admin เพื่อ enumerate ทุก shared drive (useDomainAdminAccess)
- impersonate organizer ของแต่ละ drive เพื่อไล่ไฟล์ข้างใน (แบบเดียวกับ GAM)

หมายเหตุ Shared Drive:
- ไฟล์เป็นของ "drive" ไม่ใช่ของคน -> ไม่มี owner รายไฟล์
- ใช้ lastModifyingUser (คนแก้ล่าสุด) + member ระดับ drive (permission ของแต่ละคน) แทน
"""

import time
import random
import functools

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

FOLDER_MIME = "application/vnd.google-apps.folder"

# scope เดียวก็พอสำหรับ drives/files/permissions (ต้อง authorize ใน Admin console DWD)
SCOPES = ["https://www.googleapis.com/auth/drive"]

FILE_FIELDS = (
    "nextPageToken, files(id,name,mimeType,size,modifiedTime,createdTime,"
    "lastModifyingUser(displayName,emailAddress),webViewLink,shortcutDetails,parents)"
)


def _svc_for(sa_file, subject):
    """สร้าง Drive service โดย impersonate subject (email ในโดเมน)"""
    creds = service_account.Credentials.from_service_account_file(
        sa_file, scopes=SCOPES, subject=subject
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _svc_cached(sa_file):
    """คืน factory ที่ cache service ต่อ subject (กันสร้างซ้ำ)"""
    cache = {}

    def get(subject):
        if subject not in cache:
            cache[subject] = _svc_for(sa_file, subject)
        return cache[subject]

    return get


def _exec(request, max_tries=6):
    """execute + exponential backoff สำหรับ 403/429/5xx (quota / rate limit)"""
    for i in range(max_tries):
        try:
            return request.execute()
        except HttpError as e:
            status = getattr(e.resp, "status", None)
            if status in (403, 429, 500, 502, 503) and i < max_tries - 1:
                time.sleep((2 ** i) + random.random())
                continue
            raise


def list_shared_drives(admin_svc):
    """enumerate ทุก shared drive ในโดเมน (ต้องเป็น admin ที่มีสิทธิ์ Manage shared drives)"""
    drives = []
    page_token = None
    while True:
        resp = _exec(admin_svc.drives().list(
            pageSize=100,
            useDomainAdminAccess=True,
            fields="nextPageToken, drives(id,name,createdTime)",
            pageToken=page_token,
        ))
        drives.extend(resp.get("drives", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return drives


def list_drive_members(admin_svc, drive_id):
    """member/permission ระดับ drive (organizer/fileOrganizer/writer/commenter/reader)"""
    members = []
    page_token = None
    while True:
        resp = _exec(admin_svc.permissions().list(
            fileId=drive_id,
            useDomainAdminAccess=True,
            supportsAllDrives=True,
            pageSize=100,
            fields="nextPageToken, permissions(id,type,emailAddress,role,displayName,domain,deleted)",
            pageToken=page_token,
        ))
        members.extend(resp.get("permissions", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return members


def _list_all_items(svc, drive_id, drive_name):
    """
    ดึง 'ทุก' item ใน drive ด้วย query เดียว (paginate ทีละ 1000)
    เร็วกว่าการไล่ทีละโฟลเดอร์มาก: จาก O(folders) call เหลือ O(files/1000)
    """
    items = []
    page_token = None
    while True:
        resp = _exec(svc.files().list(
            q="trashed=false",
            corpora="drive",
            driveId=drive_id,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
            pageSize=1000,
            fields=FILE_FIELDS,
            pageToken=page_token,
        ))
        items.extend(resp.get("files", []))
        print(f"       {drive_name}: {len(items)} รายการ…", flush=True)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items


def pick_crawl_subject(members, internal_domains, admin_email):
    """
    เลือก user ที่จะ impersonate ไปไล่ไฟล์:
    - อันดับแรก organizer ที่เป็นโดเมนภายใน
    - รองลงมา fileOrganizer/writer ภายใน
    - ท้ายสุด fallback = admin_email
    """
    prio = {"organizer": 0, "fileOrganizer": 1, "writer": 2}
    best = None
    best_rank = 99
    for m in members:
        if m.get("type") != "user" or not m.get("emailAddress"):
            continue
        email = m["emailAddress"]
        dom = email.split("@")[-1].lower()
        if dom not in internal_domains:
            continue
        rank = prio.get(m.get("role"), 50)
        if rank < best_rank:
            best_rank = rank
            best = email
    return best or admin_email


def walk_drive(svc, drive_id, drive_name):
    """
    ดึงทุก item ใน drive ครั้งเดียว แล้วประกอบเป็น tree ใน memory (เร็ว)
    คืน (root_node, file_count, folder_count, total_size)
    """
    root = {"id": drive_id, "name": drive_name, "type": "drive",
            "path": drive_name, "children": []}
    nodes = {drive_id: root}

    items = _list_all_items(svc, drive_id, drive_name)

    # สร้าง node ทุกตัวก่อน
    for f in items:
        is_folder = f.get("mimeType") ==
