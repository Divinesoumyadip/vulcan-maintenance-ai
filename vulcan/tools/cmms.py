"""CMMS spares lookup + feedback-learning persistence.

query_spares: fuzzy lookup against data/spares.json (the mock CMMS).
record_feedback / get_feedback_history: SQLite persistence so VULCAN's
Section-13 learning loop genuinely survives across sessions.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from vulcan.config import SPARES_DB_PATH, FEEDBACK_DB_PATH


# ───────────────────────── CMMS / SPARES ─────────────────────────

def query_spares(search_term: str) -> dict:
    if not SPARES_DB_PATH.exists():
        return {"status": "NO_DATA",
                "message": "data/spares.json not found — raise an "
                           "INFORMATION GAP and direct user to CMMS."}
    catalog = json.loads(SPARES_DB_PATH.read_text(encoding="utf-8"))
    terms = [t for t in search_term.lower().split() if len(t) > 2]
    hits = []
    for item in catalog:
        haystack = " ".join(
            str(item.get(k, "")) for k in
            ("description", "category", "compatible_equipment")
        ).lower()
        words = set(re.findall(r"[a-z0-9_-]+", haystack))
        score = sum(1 for t in terms if t in words)
        # require >=2 matched terms for multi-word queries (precision guard)
        min_score = 2 if len(terms) >= 3 else 1
        if score >= min_score:
            hits.append((score, item))
    hits.sort(key=lambda x: -x[0])
    return {
        "status": "OK" if hits else "NO_MATCH",
        "search_term": search_term,
        "matches": [h[1] for h in hits[:6]],
        "evidence_tier": 1,
        "note": "Live CMMS stock read → Tier-1 evidence. Lead times in this "
                "demo catalog are SYNTHETIC sample values.",
    }


# ───────────────────────── FEEDBACK LOOP ─────────────────────────

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(FEEDBACK_DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS feedback (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               created_at TEXT NOT NULL,
               report_id TEXT,
               equipment_class TEXT,
               failure_mode TEXT,
               vulcan_confidence TEXT,
               verdict TEXT CHECK(verdict IN
                   ('CONFIRMED','PARTIAL','INCORRECT')),
               correction_detail TEXT
           )"""
    )
    return conn


def record_feedback(report_id: str, equipment_class: str, failure_mode: str,
                    verdict: str, vulcan_confidence: str = "",
                    correction_detail: str = "") -> dict:
    verdict = verdict.upper().strip()
    if verdict not in {"CONFIRMED", "PARTIAL", "INCORRECT"}:
        return {"status": "ERROR",
                "message": "verdict must be CONFIRMED, PARTIAL or INCORRECT"}
    with closing(_conn()) as conn, conn:
        conn.execute(
            "INSERT INTO feedback (created_at, report_id, equipment_class, "
            "failure_mode, vulcan_confidence, verdict, correction_detail) "
            "VALUES (?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), report_id,
             equipment_class, failure_mode, vulcan_confidence, verdict,
             correction_detail),
        )
    return {"status": "STORED", "report_id": report_id, "verdict": verdict,
            "note": "Persisted to data/feedback.sqlite3 — this record will be "
                    "retrievable in future sessions via get_feedback_history."}


def get_feedback_history(equipment_class: str = "",
                         failure_mode: str = "") -> dict:
    if not FEEDBACK_DB_PATH.exists():
        return {"status": "EMPTY", "records": []}
    with closing(_conn()) as conn:
        q = ("SELECT created_at, report_id, equipment_class, failure_mode, "
             "vulcan_confidence, verdict, correction_detail FROM feedback "
             "WHERE 1=1")
        params: list[str] = []
        if equipment_class:
            q += " AND equipment_class LIKE ?"
            params.append(f"%{equipment_class}%")
        if failure_mode:
            q += " AND failure_mode LIKE ?"
            params.append(f"%{failure_mode}%")
        q += " ORDER BY created_at DESC LIMIT 25"
        rows = conn.execute(q, params).fetchall()
    cols = ["created_at", "report_id", "equipment_class", "failure_mode",
            "vulcan_confidence", "verdict", "correction_detail"]
    return {
        "status": "OK",
        "records": [dict(zip(cols, r)) for r in rows],
        "evidence_tier": 2,
        "note": "Engineer-confirmed prior diagnoses → Tier-2 evidence "
                "(VULCAN Sec 6, feedback loop). Apply Section-13 band "
                "adjustments based on these verdicts.",
    }
