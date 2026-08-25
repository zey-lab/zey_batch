"""Backup SQLite database to Google Drive and clean local files."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "customer_master.sqlite3"
ARCHIVE_DIR = ROOT / "data" / "archive"
DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "1eF2LPhjOjGKHJUlgZ4DjYH1UlR4--91K")
GWS_PATH = os.getenv("GWS_PATH", "/opt/data/.local/bin/gws")
GWS_HOME = os.getenv("GWS_HOME", "/opt/data")
INCOMING_DIR = ROOT / "data" / "incoming"


def run_gws(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [GWS_PATH, *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": GWS_HOME},
    )


def backup_database() -> dict:
    """Upload timestamped SQLite backup to Google Drive."""
    if not DB_PATH.exists():
        return {"status": "error", "message": "Database not found"}

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_name = f"customer_master_{timestamp}.sqlite3"
    backup_path = ARCHIVE_DIR / backup_name
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    # Create backup copy
    import shutil
    shutil.copy2(str(DB_PATH), str(backup_path))

    # Upload to Drive
    try:
        result = run_gws(
            "drive", "+upload",
            str(backup_path),
            "--parent", DRIVE_FOLDER_ID,
            "--name", backup_name,
            "--format", "json",
        )
        # Parse JSON output (gws outputs a single JSON object)
        stdout = result.stdout.strip()
        upload_info = json.loads(stdout) if stdout.startswith("{") else {}

        return {
            "status": "ok",
            "file": backup_name,
            "size_bytes": backup_path.stat().st_size,
            "drive_file_id": upload_info.get("id"),
        }
    except subprocess.CalledProcessError as e:
        return {"status": "error", "message": f"Drive upload failed: {e.stderr[:200]}"}


def cleanup_incoming() -> dict:
    """Remove processed files from data/incoming/ after successful sync."""
    removed = []
    if INCOMING_DIR.exists():
        for f in INCOMING_DIR.glob("vagaro_*_latest.json"):
            f.unlink()
            removed.append(f.name)

    return {"removed": removed}


def cleanup_archive(keep_days: int = 30) -> dict:
    """Remove local archive backups older than keep_days."""
    removed = []
    if ARCHIVE_DIR.exists():
        cutoff = datetime.utcnow().timestamp() - (keep_days * 86400)
        for f in ARCHIVE_DIR.glob("*.sqlite3"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed.append(f.name)

    return {"removed": removed, "kept_days": keep_days}


def backup_and_cleanup() -> dict[str, object]:
    """Full backup cycle: upload to Drive, then clean local files."""
    report: dict[str, object] = {"started_at": datetime.utcnow().isoformat()}

    # Step 1: Backup database to Drive
    report["backup"] = backup_database()

    # Step 2: Clean incoming files
    report["cleanup_incoming"] = cleanup_incoming()

    # Step 3: Clean old archives
    report["cleanup_archive"] = cleanup_archive()

    report["status"] = "ok"
    report["completed_at"] = datetime.utcnow().isoformat()
    return report


if __name__ == "__main__":
    print(json.dumps(backup_and_cleanup(), indent=2))
