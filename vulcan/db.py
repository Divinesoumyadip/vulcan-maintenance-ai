"""Production storage layer (v11) — SQLite in WAL mode.

WHY THIS EXISTS
---------------
Through v10, the action-side state of the system lived in flat files:
work orders in `work_orders.json` (read-modify-write of the whole file),
notifications in a JSONL append log, sentinel de-dup state in a JSON blob.
That is fine for a 5-asset demo and indefensible for a plant: no real
transactions, O(n) rewrites per order, no cross-process safety beyond
"atomic rename and hope", no retention, no indexed queries.

v11 moves all three onto a single SQLite database in WAL mode:

  * **Transactions** — an escalation (read priority, bump, write note) is
    one atomic unit; a crash mid-way leaves the previous committed state.
  * **Concurrent safety** — WAL gives many readers + one writer with
    busy-timeout retry; the UI, the daemon, and the headless service can
    share the database. In-process writes are additionally serialized by
    a module lock.
  * **Scale** — indexed lookups (open order per asset/parameter, role
    feeds) instead of loading the whole ledger per call.
  * **Operability** — one file to back up (`data/vulcan.db`), a
    `VULCAN_DB_PATH` knob to relocate it, and a documented upgrade path
    to PostgreSQL/TimescaleDB (see PRODUCTION.md) since the schema is
    deliberately portable SQL.

HONESTY UNCHANGED: this layer stores what the deterministic layers
produced; nothing is summarized, rewritten, or invented on the way in
or out.

MIGRATION: on first open, any legacy `work_orders.json`,
`notifications.jsonl`, or `alerts/sentinel_state.json` found next to the
database is imported once and the file renamed `*.migrated` — upgrading
a v10 install loses nothing.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from vulcan.config import DATA_DIR

_write_lock = threading.Lock()
_schema_ready: set[str] = set()
_schema_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_orders (
    id            TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    updated_at    TEXT,
    equipment_id  TEXT NOT NULL,
    parameter     TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL,
    priority      TEXT NOT NULL,
    status        TEXT NOT NULL,
    details       TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT 'agent',
    evidence_ref  TEXT NOT NULL DEFAULT '',
    escalation_note TEXT
);
CREATE INDEX IF NOT EXISTS idx_wo_open
    ON work_orders (equipment_id, parameter, status);
CREATE INDEX IF NOT EXISTS idx_wo_status ON work_orders (status);

CREATE TABLE IF NOT EXISTS notifications (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    event_type TEXT NOT NULL,
    severity   TEXT NOT NULL,
    roles      TEXT NOT NULL,          -- JSON array
    title      TEXT NOT NULL,
    body       TEXT NOT NULL DEFAULT '',
    ref        TEXT NOT NULL DEFAULT '',
    webhook    TEXT NOT NULL DEFAULT 'disabled'
);
CREATE INDEX IF NOT EXISTS idx_notif_at ON notifications (at);

CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
"""


def db_path() -> Path:
    """Resolved at CALL time so tests/operators can repoint without a
    process restart (same policy as every v10 knob)."""
    p = os.environ.get("VULCAN_DB_PATH", "").strip()
    return Path(p) if p else (DATA_DIR / "vulcan.db")


def connect() -> sqlite3.Connection:
    """Open a connection with production pragmas; ensure schema +
    one-time legacy migration for this database file."""
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    key = str(p.resolve())
    if key not in _schema_ready:
        with _schema_lock:
            if key not in _schema_ready:
                conn.executescript(_SCHEMA)
                conn.commit()
                _migrate_legacy(conn)
                _schema_ready.add(key)
    return conn


# ───────────────────────── legacy migration ─────────────────────────
def _migrate_legacy(conn: sqlite3.Connection) -> None:
    """Import v10 flat files exactly once, then rename them *.migrated."""
    base = db_path().parent
    # work_orders.json
    wo_file = base / "work_orders.json"
    if wo_file.exists() and conn.execute(
            "SELECT COUNT(*) FROM work_orders").fetchone()[0] == 0:
        try:
            for w in json.loads(wo_file.read_text(encoding="utf-8")):
                conn.execute(
                    "INSERT OR IGNORE INTO work_orders (id, created_at, "
                    "updated_at, equipment_id, parameter, title, priority, "
                    "status, details, source, evidence_ref, escalation_note) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (w.get("id"), w.get("created_at"), w.get("updated_at"),
                     w.get("equipment_id", ""), w.get("parameter", ""),
                     w.get("title", ""), w.get("priority", "HIGH"),
                     w.get("status", "OPEN"), w.get("details", ""),
                     w.get("source", "agent"), w.get("evidence_ref", ""),
                     w.get("escalation_note")))
            conn.commit()
            wo_file.rename(wo_file.with_suffix(".json.migrated"))
        except (json.JSONDecodeError, OSError):
            pass                       # never block startup on bad legacy
    # notifications.jsonl
    nf_file = base / "notifications.jsonl"
    if nf_file.exists() and conn.execute(
            "SELECT COUNT(*) FROM notifications").fetchone()[0] == 0:
        try:
            for line in nf_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                conn.execute(
                    "INSERT INTO notifications (at, event_type, severity, "
                    "roles, title, body, ref, webhook) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (r.get("at", ""), r.get("event_type", ""),
                     r.get("severity", ""),
                     json.dumps(r.get("roles", [])), r.get("title", ""),
                     r.get("body", ""), r.get("ref", ""),
                     r.get("webhook", "disabled")))
            conn.commit()
            nf_file.rename(nf_file.with_suffix(".jsonl.migrated"))
        except OSError:
            pass
    # sentinel state
    st_file = base / "alerts" / "sentinel_state.json"
    if st_file.exists() and conn.execute(
            "SELECT COUNT(*) FROM kv WHERE k='sentinel_state'"
    ).fetchone()[0] == 0:
        try:
            json.loads(st_file.read_text(encoding="utf-8"))  # validate
            conn.execute(
                "INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)",
                ("sentinel_state",
                 st_file.read_text(encoding="utf-8")))
            conn.commit()
            st_file.rename(st_file.with_suffix(".json.migrated"))
        except (json.JSONDecodeError, OSError):
            pass


# ───────────────────────── kv (sentinel state) ─────────────────────────
def kv_get(key: str, default: str = "") -> str:
    with closing(connect()) as conn:
        row = conn.execute("SELECT v FROM kv WHERE k=?", (key,)).fetchone()
        return row["v"] if row else default


def kv_set(key: str, value: str) -> None:
    with _write_lock, closing(connect()) as conn:
        conn.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?,?)",
                     (key, value))
        conn.commit()


# ───────────────────────── work orders ─────────────────────────
def _row_to_wo(row: sqlite3.Row) -> dict:
    d = {k: row[k] for k in row.keys()}
    if d.get("updated_at") is None:
        d.pop("updated_at", None)
    if d.get("escalation_note") is None:
        d.pop("escalation_note", None)
    return d


def wo_find_open(equipment_id: str, parameter: str) -> dict | None:
    with closing(connect()) as conn:
        row = conn.execute(
            "SELECT * FROM work_orders WHERE equipment_id=? AND parameter=? "
            "AND status IN ('OPEN','IN_PROGRESS') "
            "ORDER BY created_at DESC LIMIT 1",
            (equipment_id, parameter)).fetchone()
        return _row_to_wo(row) if row else None


def wo_insert(wo: dict) -> None:
    with _write_lock, closing(connect()) as conn:
        conn.execute(
            "INSERT INTO work_orders (id, created_at, equipment_id, "
            "parameter, title, priority, status, details, source, "
            "evidence_ref) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (wo["id"], wo["created_at"], wo["equipment_id"],
             wo.get("parameter", ""), wo["title"], wo["priority"],
             wo["status"], wo.get("details", ""), wo.get("source", "agent"),
             wo.get("evidence_ref", "")))
        conn.commit()


def wo_update(wo_id: str, **fields) -> dict | None:
    """Transactional partial update; returns the updated row or None."""
    if not fields:
        return None
    cols = ", ".join(f"{k}=?" for k in fields)
    with _write_lock, closing(connect()) as conn:
        cur = conn.execute(
            f"UPDATE work_orders SET {cols} WHERE id=?",
            (*fields.values(), wo_id))
        conn.commit()
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM work_orders WHERE id=?",
                           (wo_id,)).fetchone()
        return _row_to_wo(row)


def wo_list(status: str = "", equipment_id: str = "",
            limit: int = 50) -> list[dict]:
    q, args = "SELECT * FROM work_orders", []
    conds = []
    if status:
        conds.append("status=?")
        args.append(status)
    if equipment_id:
        conds.append("equipment_id=?")
        args.append(equipment_id)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with closing(connect()) as conn:
        return [_row_to_wo(r) for r in conn.execute(q, args).fetchall()]


def wo_count() -> int:
    with closing(connect()) as conn:
        return conn.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0]


# ───────────────────────── notifications ─────────────────────────
def notif_insert(rec: dict) -> None:
    with _write_lock, closing(connect()) as conn:
        conn.execute(
            "INSERT INTO notifications (at, event_type, severity, roles, "
            "title, body, ref, webhook) VALUES (?,?,?,?,?,?,?,?)",
            (rec["at"], rec["event_type"], rec["severity"],
             json.dumps(rec["roles"]), rec["title"], rec.get("body", ""),
             rec.get("ref", ""), rec.get("webhook", "disabled")))
        conn.commit()


def notif_list(role: str = "", limit: int = 50) -> list[dict]:
    with closing(connect()) as conn:
        rows = conn.execute(
            "SELECT * FROM notifications ORDER BY seq DESC LIMIT ?",
            (max(limit * 4, limit),)).fetchall()   # over-fetch for role filter
    out = []
    for r in rows:
        rec = {k: r[k] for k in r.keys() if k != "seq"}
        rec["roles"] = json.loads(rec["roles"])
        if role and role not in rec["roles"]:
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def notif_prune(before_iso: str) -> int:
    with _write_lock, closing(connect()) as conn:
        cur = conn.execute("DELETE FROM notifications WHERE at < ?",
                           (before_iso,))
        conn.commit()
        return cur.rowcount
