"""Role-based notification router (v10) — FR7 "user-specific notifications".

THE GAP THIS CLOSES
-------------------
Through v9, autonomous output ended at alert FILES plus a UI inbox. The
problem statement's FR7 asks for *user-specific notifications*: a CRITICAL
breach must reach the shift engineer AND the supervisor; a predicted
failure must reach the engineer and the maintenance planner; a zero-stock
spare on a critical asset must reach procurement. Nobody routed anything.

This module is that router. Every autonomous decision (anomaly alert,
predictive-failure alert, auto work order, SLA breach, procurement risk)
is mapped to the ROLES who must act on it via an explicit, auditable
routing matrix, and persisted to the production SQLite (WAL) store
(v11 — was a JSONL file through v10; legacy files auto-migrate). If
VULCAN_WEBHOOK_URL is set, each notification is also POSTed to that
endpoint (Slack/Teams/SMS-gateway style) — fail-safe: a webhook error
can never break the autonomy loop, it is recorded on the record instead.
v11 security: if VULCAN_WEBHOOK_SECRET is set, every POST carries an
HMAC-SHA256 signature header (X-Vulcan-Signature) so the receiver can
verify authenticity — an unauthenticated alert sink is an injection
vector in a plant network.

Honesty: notifications carry the evidence reference (alert file / work
order id) they trace back to. The router never invents content — it only
routes what the deterministic layers produced.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

from vulcan import db
from vulcan.config import webhook_secret, webhook_url

ROLES = ("engineer", "supervisor", "planner", "procurement")

# Explicit routing matrix — auditable, single source of truth.
ROUTING: dict[str, list[str]] = {
    "ANOMALY_CRITICAL":   ["engineer", "supervisor"],
    "ANOMALY_WARNING":    ["engineer"],
    "PREDICTIVE_FAILURE": ["engineer", "planner"],
    "DATA_QUALITY":       ["engineer"],            # dead sensor = unmonitored
    "WORK_ORDER_RAISED":  ["supervisor"],
    "WORK_ORDER_SLA_BREACH": ["supervisor", "planner"],
    "PROCUREMENT_RISK":   ["procurement", "planner"],
    "RESOLVED":           ["engineer"],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sign_payload(body: bytes, secret: str) -> str:
    """HMAC-SHA256 signature for webhook authenticity (v11)."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body,
                                hashlib.sha256).hexdigest()


def _post_webhook(record: dict) -> str:
    """Best-effort POST to VULCAN_WEBHOOK_URL. Never raises. Signed with
    X-Vulcan-Signature when VULCAN_WEBHOOK_SECRET is configured."""
    url = webhook_url()
    if not url:
        return "disabled"
    try:
        import urllib.request
        body = json.dumps(record).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        secret = webhook_secret()
        if secret:
            headers["X-Vulcan-Signature"] = sign_payload(body, secret)
        req = urllib.request.Request(url, data=body, headers=headers,
                                     method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            return f"sent:{resp.status}"
    except Exception as exc:                       # fail-safe by design
        return f"failed:{type(exc).__name__}"


def notify(event_type: str, severity: str, title: str, body: str = "",
           ref: str = "", roles: list[str] | None = None) -> dict:
    """Route one autonomous event to its roles and persist it (SQLite WAL).

    Returns the persisted record (including webhook delivery status).
    Storage location follows VULCAN_DB_PATH at call time."""
    record = {
        "at": _now(),
        "event_type": event_type,
        "severity": severity,
        "roles": roles if roles is not None
        else ROUTING.get(event_type, ["engineer"]),
        "title": title,
        "body": body,
        "ref": ref,
    }
    record["webhook"] = _post_webhook(record)
    db.notif_insert(record)
    return record


def read_notifications(role: str = "", limit: int = 50) -> list[dict]:
    """Most-recent-first notification feed, optionally filtered by role."""
    return db.notif_list(role=role, limit=limit)
