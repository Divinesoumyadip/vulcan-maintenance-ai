"""Work-order store — closes the loop from alert to ACTION.

v8 introduced the ledger; v9 made escalations explicit; v11 moves it from
a rewritten-whole-file JSON blob onto the production SQLite (WAL) store
(`vulcan/db.py`): transactional escalations, indexed open-order lookups,
many readers + serialized writers, one backup file. Legacy
`work_orders.json` is auto-migrated on first run.

Behavior is contract-stable: the AUTOPILOT auto-raises on CRITICAL
(de-duplicated per asset/parameter while one is open; higher-priority
re-triggers ESCALATE instead of being swallowed), and the agent can
raise/list/update orders as genuine tools. Provenance (evidence_ref,
trigger details) travels with every action — nothing is invented.
"""
from __future__ import annotations

from datetime import datetime, timezone

from vulcan import db

_VALID_PRIORITIES = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
_VALID_STATUSES = {"OPEN", "IN_PROGRESS", "DONE", "CANCELLED"}
_PRIORITY_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_work_order(equipment_id: str, title: str, priority: str = "HIGH",
                      parameter: str = "", details: str = "",
                      source: str = "agent",
                      evidence_ref: str = "") -> dict:
    """Raise a work order. Idempotent per (asset, parameter) while OPEN —
    a second identical trigger returns the existing order; a HIGHER-priority
    trigger transactionally escalates the open order and records why."""
    priority = priority.upper().strip()
    if priority not in _VALID_PRIORITIES:
        return {"status": "ERROR",
                "message": f"priority must be one of {sorted(_VALID_PRIORITIES)}"}
    existing = db.wo_find_open(equipment_id, parameter)
    if existing:
        if (_PRIORITY_RANK[priority]
                > _PRIORITY_RANK.get(existing["priority"], 0)):
            old = existing["priority"]
            note = (f"priority escalated {old} → {priority} by a new "
                    f"{source} trigger"
                    + (f" (evidence: {evidence_ref})" if evidence_ref else ""))
            updated = db.wo_update(existing["id"], priority=priority,
                                   updated_at=_now(), escalation_note=note)
            return {"status": "ESCALATED", "work_order": updated,
                    "note": f"open order existed at {old}; priority raised "
                            f"to {priority}, not duplicated"}
        return {"status": "DUPLICATE_OPEN", "work_order": existing,
                "note": "an open work order already covers this "
                        "asset/parameter — not duplicated"}
    wo_id = f"WO-{datetime.now(timezone.utc).strftime('%Y%m%d')}-" \
            f"{db.wo_count() + 1:04d}"
    wo = {"id": wo_id, "created_at": _now(),
          "equipment_id": equipment_id, "parameter": parameter,
          "title": title, "priority": priority, "status": "OPEN",
          "details": details,
          "source": source,            # 'autopilot' | 'agent' | 'engineer'
          "evidence_ref": evidence_ref}
    db.wo_insert(wo)
    return {"status": "CREATED", "work_order": wo}


def list_work_orders(status: str = "", equipment_id: str = "") -> dict:
    orders = db.wo_list(status=status.upper().strip() if status else "",
                        equipment_id=equipment_id, limit=50)
    return {"status": "OK", "n": len(orders), "work_orders": orders,
            "note": "Work-order ledger (SQLite WAL, data/vulcan.db) — "
                    "Tier-1 live read of tracked actions."}


def update_work_order(work_order_id: str, status: str) -> dict:
    status = status.upper().strip()
    if status not in _VALID_STATUSES:
        return {"status": "ERROR",
                "message": f"status must be one of {sorted(_VALID_STATUSES)}"}
    updated = db.wo_update(work_order_id, status=status, updated_at=_now())
    if updated is None:
        return {"status": "NOT_FOUND",
                "message": f"no work order {work_order_id}"}
    return {"status": "UPDATED", "work_order": updated}
