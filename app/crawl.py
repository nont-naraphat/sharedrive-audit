"""
crawl.py — CLI สำหรับรัน crawl ครั้งเดียว (เช่น รันมือ หรือใน cron ภายนอก)
เรียก run_audit ตัวเดียวกับที่ API ใช้

  python crawl.py --sa sa.json --admin admin@office21sun.com \
      --domains office21sun.com,21sunpassion.com,sunsusolution.com --out ../output
"""

import os
import argparse

from audit_service import run_audit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sa", default=os.getenv("SA_FILE", "sa.json"))
    ap.add_argument("--admin", default=os.getenv("ADMIN_EMAIL"))
    ap.add_argument("--domains", default=os.getenv(
        "INTERNAL_DOMAINS", "office21sun.com,21sunpassion.com,sunsusolution.com"))
    ap.add_argument("--out", default=os.getenv("OUTPUT_DIR", "../output"))
    args = ap.parse_args()
    if not args.admin:
        ap.error("ต้องระบุ --admin หรือ env ADMIN_EMAIL")

    internal = {d.strip().lower() for d in args.domains.split(",") if d.strip()}
    res = run_audit(args.sa, args.admin, internal, args.out)
    print(f"[✓] {res}")
    print(f"    -> {os.path.join(args.out, 'audit.json')}")
    print(f"    -> {os.path.join(args.out, 'audit.xlsx')}")


if __name__ == "__main__":
    main()
