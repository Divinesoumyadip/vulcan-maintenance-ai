"""Retention pruning (v11) — unbounded growth is an outage, slowly.

Each autonomous cycle calls prune_retention(): alert/predict report files
older than VULCAN_RETENTION_DAYS are deleted from data/alerts/, and
notification rows older than the same horizon are deleted from the
database. Work orders are NEVER pruned — they are the audit trail of
actions taken. 0 days disables pruning entirely.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from vulcan import db
from vulcan.config import DATA_DIR, retention_days


def prune_retention(alerts_dir: Path | None = None) -> dict:
    days = retention_days()
    if days <= 0:
        return {"pruned_alert_files": 0, "pruned_notifications": 0,
                "disabled": True}
    alerts_dir = alerts_dir or (DATA_DIR / "alerts")
    cutoff_ts = time.time() - days * 86400
    pruned_files = 0
    if alerts_dir.exists():
        for f in list(alerts_dir.glob("ALERT_*.md")) + \
                 list(alerts_dir.glob("PREDICT_*.md")):
            try:
                if f.stat().st_mtime < cutoff_ts:
                    f.unlink()
                    pruned_files += 1
            except OSError:
                continue
    cutoff_iso = (datetime.now(timezone.utc)
                  - timedelta(days=days)).isoformat(timespec="seconds")
    pruned_rows = db.notif_prune(cutoff_iso)
    return {"pruned_alert_files": pruned_files,
            "pruned_notifications": pruned_rows}
