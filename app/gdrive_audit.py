"""
gdrive_audit.py
Core library สำหรับ audit Google Shared Drives ทั้งโดเมน
"""

import time
import random
import functools

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

FOLDER_MIME = "application/vnd.google-apps.folder"

SCOPES = ["https://www.googleapis.com/auth/drive"]

FILE_FIELDS = (
    "nextPageToken, files(id,name,mimeType,size,modifiedTime,createdTime,"
    "lastModifyingUser(displayName,emailAddress),webViewLink,shortcutDetails,parents)"
)


def _svc_for(sa_file, subject):
    creds = service_account.Credentials.from_service_account_file(
        sa_file, scopes=SCOPES, subject=subject
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _svc_cached(sa_file):
    cache = {}

    def get(subject):
        if subject not in cache:
            cache[subject] = _svc_for(sa_file, subject)
        return cache[subject]

    return get


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


def list_shared_drives(admin_svc):
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


ROLE_TH = {
    "owner": "เจ้าของ",
    "organizer": "ผู้จัดการ",
    "fileOrganizer": "จัดการไฟล์",
    "writer": "แก้ไขได้",
    "commenter": "คอมเมนต์",
    "reader": "ดูอย่างเดียว",
}

PERM_FIELDS = ("permissions(id,type,emailAddress,role,displayName,domain,"
               "permissionDetails(inherited,inheritedFrom,role))")


def is_external(perm, internal_domains):
    if perm.get("type") == "anyone":
        return True
    email = perm.get("emailAddress") or ""
    dom = (perm.get("domain") or (email.split("@")[-1] if "@" in email else "")).lower()
    return bool(dom) and dom not in internal_domains


def is_direct(perm):
    details = perm.get("permissionDetails")
    if not details:
        return True
    return any(not d.get("inherited") for d in details)


def list_item_permissions(svc, file_id):
    perms = []
    page_token = None
    while True:
        resp = _exec(svc.permissions().list(
            fileId=file_id,
            supportsAllDrives=True,
            pageSize=100,
            fields="nextPageToken, " + PERM_FIELDS,
            pageToken=page_token,
        ))
        perms.extend(resp.get("permissions", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return perms


def _list_all_items_with_perms(svc, drive_id, drive_name):
    fields = ("nextPageToken, files(id,name,mimeType,parents,"
              + PERM_FIELDS + ")")
    items = []
    page_token = None
    while True:
        resp = _exec(svc.files().list(
            q="trashed=false", corpora="drive", driveId=drive_id,
            includeItemsFromAllDrives=True, supportsAllDrives=True,
            pageSize=1000, fields=fields, pageToken=page_token,
        ))
        items.extend(resp.get("files", []))
        print(f"       [perms] {drive_name}: {len(items)} รายการ…", flush=True)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return items


def iter_permission_rows(sa_file, admin_email, internal_domains, progress=None):
    get_svc = _svc_cached(sa_file)
    admin_svc = get_svc(admin_email)
    drives = list_shared_drives(admin_svc)
    total = len(drives)
    count = 0

    for i, d in enumerate(drives, 1):
        drive_id, dname = d["id"], d.get("name", "(no name)")
        print(f"  -> [perms {i}/{total}] {dname}", flush=True)
        members = list_drive_members(admin_svc, drive_id)
        subject = pick_crawl_subject(members, internal_domains, admin_email)
        try:
            svc = get_svc(subject)
            items = _list_all_items_with_perms(svc, drive_id, dname)
        except HttpError as e:
            print(f"     [!] ข้าม {dname} ({e})", flush=True)
            continue

        by_id = {f["id"]: f for f in items}

        def path_of(fid, _seen=None):
            f = by_id.get(fid)
            if not f:
                return dname
            parent = (f.get("parents") or [drive_id])[0]
            if parent == drive_id or parent not in by_id:
                return dname + "/" + f.get("name", "")
            return path_of(parent) + "/" + f.get("name", "")

        for f in items:
            is_folder = f.get("mimeType") == FOLDER_MIME
            path = path_of(f["id"])
            for p in (f.get("permissions") or []):
                dom = p.get("domain") or ""
                if not dom and p.get("emailAddress", "").find("@") >= 0:
                    dom = p["emailAddress"].split("@")[-1]
                yield {
                    "drive": dname,
                    "path": path,
                    "type": "folder" if is_folder else "file",
                    "name": f.get("name", ""),
                    "item_id": f["id"],
                    "member": p.get("emailAddress") or p.get("type", ""),
                    "member_type": p.get("type", ""),
                    "role": p.get("role", ""),
                    "role_th": ROLE_TH.get(p.get("role", ""), p.get("role", "")),
                    "inherited": "no" if is_direct(p) else "yes",
                    "external": "yes" if is_external(p, internal_domains) else "no",
                    "domain": dom,
                }
                count += 1
                if progress and count % 5000 == 0:
                    progress(count)
    if progress:
        progress(count)


def _list_all_items(svc, drive_id, drive_name):
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
    root = {"id": drive_id, "name": drive_name, "type": "drive",
            "path": drive_name, "children": []}
    nodes = {drive_id: root}

    items = _list_all_items(svc, drive_id, drive_name)

    for f in items:
        is_folder = f.get("mimeType") == FOLDER_MIME
        nodes[f["id"]] = {
            "id": f["id"],
            "name": f.get("name", ""),
            "type": "folder" if is_folder else "file",
            "mimeType": f.get("mimeType"),
            "size": int(f["size"]) if f.get("size") else None,
            "createdTime": f.get("createdTime"),
            "modifiedTime": f.get("modifiedTime"),
            "lastModifiedBy": (f.get("lastModifyingUser") or {}).get("emailAddress"),
            "webViewLink": f.get("webViewLink"),
            "children": [] if is_folder else None,
            "_parent": (f.get("parents") or [drive_id])[0],
        }

    file_count = folder_count = total_size = 0
    for nid, node in nodes.items():
        if nid == drive_id:
            continue
        parent = nodes.get(node["_parent"], root)
        if parent.get("children") is None:
            parent = root
        parent["children"].append(node)
        if node["type"] == "folder":
            folder_count += 1
        else:
            file_count += 1
            if node["size"]:
                total_size += node["size"]

    def finalize(node, prefix):
        kids = node.get("children")
        if not kids:
            return
        kids.sort(key=lambda c: (c["type"] != "folder", c["name"].lower()))
        for c in kids:
            c["path"] = prefix + "/" + c["name"]
            c.pop("_parent", None)
            finalize(c, c["path"])

    finalize(root, drive_name)
    return root, file_count, folder_count, total_size


def audit_all(sa_file, admin_email, internal_domains):
    get_svc = _svc_cached(sa_file)
    admin_svc = get_svc(admin_email)

    result = {"drives": [], "members": {}, "summary": []}
    drives = list_shared_drives(admin_svc)
    total = len(drives)
    print(f"[i] พบ shared drive {total} ตัว", flush=True)

    for i, d in enumerate(drives, 1):
        drive_id, name = d["id"], d.get("name", "(no name)")
        print(f"  -> [{i}/{total}] {name}", flush=True)
        members = list_drive_members(admin_svc, drive_id)
        result["members"][drive_id] = members

        subject = pick_crawl_subject(members, internal_domains, admin_email)
        try:
            svc = get_svc(subject)
            root, fc, dc, size = walk_drive(svc, drive_id, name)
            print(f"     ✓ {name}: {fc} ไฟล์ / {dc} โฟลเดอร์", flush=True)
        except HttpError as e:
            print(f"     [!] ข้ามการไล่ไฟล์ ({e})", flush=True)
            root = {"id": drive_id, "name": name, "type": "drive",
                    "path": name, "children": [], "error": str(e)}
            fc = dc = size = 0

        root["fileCount"] = fc
        root["folderCount"] = dc
        root["totalSize"] = size
        root["createdTime"] = d.get("createdTime")
        root["crawledAs"] = subject
        result["drives"].append(root)
        result["summary"].append({
            "drive": name, "driveId": drive_id,
            "files": fc, "folders": dc, "totalSize": size,
            "members": len([m for m in members if not m.get("deleted")]),
            "createdTime": d.get("createdTime"),
        })

    return result
