#!/usr/bin/env python3
"""One-time backfill of existing report JSON files into dashboard.db."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.ingest_run import ingest_report_file
from dashboard.db import DashboardDB

REPORTS_DIR = Path(__file__).resolve().parent.parent / "data" / "reports"


def main():
    db = DashboardDB()
    report_files = sorted(REPORTS_DIR.glob("report_*.json"))

    if not report_files:
        print("No report files found in data/reports/")
        return

    print(f"Found {len(report_files)} report files")
    ingested = 0
    skipped = 0

    for path in report_files:
        if ingest_report_file(db, path):
            print(f"  Ingested: {path.name}")
            ingested += 1
        else:
            print(f"  Skipped:  {path.name}")
            skipped += 1

    print(f"\nDone. Ingested {ingested}, skipped {skipped}.")


if __name__ == "__main__":
    main()
