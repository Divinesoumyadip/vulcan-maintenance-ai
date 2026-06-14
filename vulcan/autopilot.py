"""VULCAN Autopilot — the autonomous loop, INSIDE the app (v8→v10).

v7's sentinel was genuinely autonomous but lived outside the product. v8
moved the loop into the running application. v9 made it tab-independent
(server daemon) and single-flight. v10 makes it PREDICTIVE and ROUTED:

    every N seconds (daemon thread / UI fragment / headless sentinel):
        1. optionally advance the simulated IoT feed (demo mode)
        2. run a sentinel pass over ALL monitored pairs — BOTH channels:
           reactive (anomaly layers) AND predictive (RUL-horizon, fires
           before any limit is breached)
        3. on NEW/ESCALATED alerts: write the structured alert report,
           append the digital logbook, ROUTE a role-specific notification
           (engineer / supervisor / planner / procurement), and on
           CRITICAL — reactive or predictive — auto-raise a de-duplicated
           work order WITH a live CMMS spares check attached
        4. flag PROCUREMENT_RISK to procurement when a critical asset's
           compatible spare shows zero stock
        5. watchdog: a CRITICAL work order still OPEN past its SLA gets a
           one-time autonomous escalation notification
        6. surface everything in the 🚨 Alerts / 🔔 Notifications UI

The decision logic is sentinel_pass() — the exact same tested function the
CLI sentinel uses — so autonomy semantics are identical headless or in-app.

Honesty by construction is unchanged: the autopilot reads sensor data and
writes alerts/work-orders/logbook/notifications. The ONLY component that
may create sensor values is the clearly-labelled SYNTHETIC demo stream.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

from sentinel import load_state, save_state, sentinel_pass, write_alert
from vulcan.config import max_alerts_per_cycle, wo_sla_minutes
from vulcan.logging_setup import get_logger, log_event
from vulcan.metrics import METRICS
from vulcan.retention import prune_retention
from vulcan.notify import notify
from vulcan.tools.cmms import query_spares
from vulcan.tools.live import append_logbook, simulate_next_reading
from vulcan.tools.workorders import create_work_order, list_work_orders

# Pairs the demo stream advances when auto_stream is on.
DEMO_STREAM_PAIRS = [("CC2-MO-01", "vibration_mm_s")]

# Single-flight lock: daemon, UI fragment and sessions never overlap.
_tick_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


def _spares_summary(equipment_id: str) -> tuple[str, bool]:
    """Live CMMS read for the asset. Returns (one-line summary, zero_stock).

    Honesty: if the catalog has no compatible record, that is stated as a
    gap ("manual spares check required") — never guessed around.
    """
    try:
        res = query_spares(equipment_id)
    except Exception as exc:
        return f"spares check failed ({exc}) — manual CMMS check required", False
    matches = [m for m in res.get("matches", [])
               if equipment_id in str(m.get("compatible_equipment", ""))]
    if not matches:
        return ("no compatible spare found in CMMS catalog — "
                "INFORMATION GAP: manual spares check required"), False
    parts = []
    zero_stock = False
    for m in matches[:3]:
        qty = m.get("on_hand_qty", "?")
        if qty == 0:
            zero_stock = True
        parts.append(f"{m.get('item_id')} \"{m.get('description', '')[:40]}\" "
                     f"qty={qty} lead={m.get('lead_time_days', '?')}d")
    return "; ".join(parts), zero_stock


def _raise_auto_wo(det: dict, ev: dict, path_name: str,
                   work_orders: list, notifications: list) -> None:
    """Auto-raise (or escalate) a CRITICAL work order, spares attached."""
    predictive = bool(ev.get("predictive"))
    spares_line, zero_stock = _spares_summary(det["equipment_id"])
    if predictive:
        rul = ev.get("rul", {})
        trigger = (f"PREDICTIVE: projected to reach critical limit in "
                   f"{rul.get('rul_point_estimate_hours', '?')} h "
                   f"(80% CI {rul.get('rul_ci80_hours', '?')}, "
                   f"{rul.get('n_readings', '?')} readings)")
        title = (f"PLAN intervention — predicted critical "
                 f"{det['parameter']} on {det['equipment_id']}")
    else:
        trigger = (f"Latest {det['latest_value']} {det['unit']} at "
                   f"{det['last_timestamp']}; layers fired: "
                   f"{[f['layer'] for f in det['layers_fired']]}")
        title = (f"Investigate CRITICAL {det['parameter']} on "
                 f"{det['equipment_id']}")
    wo = create_work_order(
        equipment_id=det["equipment_id"],
        parameter=det["parameter"],
        title=title,
        priority="CRITICAL",
        details=f"Auto-raised by autopilot. {trigger}. "
                f"Spares (live CMMS): {spares_line}.",
        source="autopilot",
        evidence_ref=path_name)
    if wo["status"] == "CREATED":
        wo_id = wo["work_order"]["id"]
        work_orders.append(wo_id)
        append_logbook(f"[AUTOPILOT] auto-raised work order {wo_id} for "
                       f"{ev['key']} — spares: {spares_line}")
        notifications.append(notify(
            "WORK_ORDER_RAISED", "CRITICAL",
            f"Work order {wo_id} auto-raised: {title}",
            body=f"{trigger}. Spares: {spares_line}.",
            ref=wo_id))
    elif wo["status"] == "ESCALATED":
        wo_id = wo["work_order"]["id"]
        work_orders.append(f"{wo_id} (escalated)")
        append_logbook(f"[AUTOPILOT] escalated open work order {wo_id} to "
                       f"CRITICAL for {ev['key']}")
        notifications.append(notify(
            "WORK_ORDER_RAISED", "CRITICAL",
            f"Open work order {wo_id} escalated to CRITICAL",
            body=trigger, ref=wo_id))
    if zero_stock:
        notifications.append(notify(
            "PROCUREMENT_RISK", "HIGH",
            f"Zero stock on compatible spare for CRITICAL asset "
            f"{det['equipment_id']}",
            body=f"Live CMMS read during auto work order: {spares_line}. "
                 f"Procurement lead time now gates the repair.",
            ref=path_name))


def _sla_watchdog(state: dict, notifications: list) -> list[str]:
    """One-time escalation for CRITICAL orders OPEN past the SLA (v10).

    De-dup ledger lives under the '_sla_notified' bookkeeping key in the
    sentinel state, so a breach is escalated exactly once per order.
    """
    notified: list = state.setdefault("_sla_notified", [])
    breached: list[str] = []
    sla_min = wo_sla_minutes()
    now = datetime.now(timezone.utc)
    try:
        open_orders = list_work_orders(status="OPEN")["work_orders"]
    except Exception:
        return breached
    for wo in open_orders:
        if wo.get("priority") != "CRITICAL" or wo["id"] in notified:
            continue
        try:
            created = datetime.fromisoformat(wo["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        age_min = (now - created).total_seconds() / 60.0
        if age_min >= sla_min:
            notified.append(wo["id"])
            breached.append(wo["id"])
            append_logbook(f"[AUTOPILOT] SLA BREACH — CRITICAL work order "
                           f"{wo['id']} still OPEN after "
                           f"{age_min:.0f} min (SLA {sla_min} min).")
            notifications.append(notify(
                "WORK_ORDER_SLA_BREACH", "CRITICAL",
                f"SLA breach: {wo['id']} still OPEN after "
                f"{age_min:.0f} min",
                body=f"{wo.get('title', '')} on {wo.get('equipment_id', '')} "
                     f"— SLA is {sla_min} min. Intervene or reassign.",
                ref=wo["id"]))
    return breached


def autopilot_tick(auto_stream: bool = False) -> dict:
    """One fully-autonomous cycle. Returns a summary for the UI strip."""
    if not _tick_lock.acquire(blocking=False):
        return {"at": _now(), "skipped": "another cycle is in flight",
                "streamed": [], "alerts": [], "resolved": [],
                "work_orders_raised": [], "notifications": [],
                "sla_breaches": []}
    try:
        return _tick(auto_stream)
    finally:
        _tick_lock.release()


def _tick(auto_stream: bool) -> dict:
    streamed: list[dict] = []
    if auto_stream:
        for eq, param in DEMO_STREAM_PAIRS:
            r = simulate_next_reading(eq, param, hours_ahead=12.0,
                                      trend_source="baseline")
            if r.get("status") == "OK":
                streamed.append(r)

    state = load_state()
    events, state = sentinel_pass(state)

    # ── v11 alert-storm guard ──
    # A pass that produces a flood of NEW alerts signals a SYSTEMIC event
    # (sensor flood, data fault, plant trip) — emitting hundreds of files,
    # notifications and work orders is noise that buries the signal. Above
    # the cap: ONE roll-up report + ONE critical notification, and the
    # per-event pipeline is skipped for this cycle (de-dup state was still
    # updated, so nothing re-fires forever).
    alert_events = [e for e in events if e["kind"] == "ALERT"]
    storm = len(alert_events) > max_alerts_per_cycle()
    if storm:
        lines = [f"# 🚨 VULCAN ALERT STORM — {len(alert_events)} new "
                 f"alerts in one pass (cap "
                 f"{max_alerts_per_cycle()})", "",
                 "A volume this size indicates a SYSTEMIC event (sensor "
                 "flood, data fault, plant trip), not "
                 f"{len(alert_events)} independent equipment problems. "
                 "Individual alert processing was suppressed for this "
                 "cycle; investigate the common cause first.", "",
                 "## Affected (key → severity)"]
        lines += [f"- `{e['key']}` → {e['severity']}"
                  f"{' (predictive)' if e.get('predictive') else ''}"
                  for e in alert_events[:200]]
        from sentinel import ALERTS_DIR
        ALERTS_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        rollup = ALERTS_DIR / f"ALERT_{stamp}_STORM_ROLLUP.md"
        tmp = rollup.with_suffix(".md.tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(rollup)
        append_logbook(f"[AUTOPILOT] ALERT STORM — {len(alert_events)} new "
                       f"alerts rolled up into {rollup.name}; per-event "
                       f"processing suppressed this cycle.")
        storm_notif = notify(
            "ANOMALY_CRITICAL", "CRITICAL",
            f"ALERT STORM: {len(alert_events)} new alerts in one pass — "
            f"systemic event suspected",
            body="Per-event processing suppressed; see roll-up report.",
            ref=rollup.name)
        save_state(state)
        summary = {"at": _now(), "streamed": streamed,
                   "alerts": [{"key": "STORM_ROLLUP",
                               "severity": "CRITICAL",
                               "predictive": False, "file": rollup.name}],
                   "resolved": [], "work_orders_raised": [],
                   "notifications": [storm_notif["event_type"]],
                   "sla_breaches": [], "alert_storm": len(alert_events)}
        METRICS.record_cycle(summary)
        import logging as _logging
        log_event(get_logger("vulcan.autopilot"), _logging.WARNING,
                  "ALERT STORM rolled up", n=len(alert_events),
                  rollup=rollup.name)
        return summary

    alerts, resolved, work_orders = [], [], []
    notifications: list[dict] = []
    for ev in events:
        if ev["kind"] == "RESOLVED":
            append_logbook(f"[AUTOPILOT] RESOLVED — {ev['key']} returned to "
                           f"NORMAL (was {ev['previous']}).")
            resolved.append(ev["key"])
            continue

        path = write_alert(ev)
        predictive = bool(ev.get("predictive"))
        label = "PREDICTIVE " if predictive else ""
        append_logbook(f"[AUTOPILOT] {label}{ev['severity']} alert on "
                       f"{ev['key']} → {path.name}")
        alerts.append({"key": ev["key"], "severity": ev["severity"],
                       "predictive": predictive, "file": path.name})

        # ── role routing (v10, FR7) ──
        det = ev["detection"]
        if predictive:
            rul = ev.get("rul", {})
            notifications.append(notify(
                "PREDICTIVE_FAILURE", ev["severity"],
                f"Predicted failure: {det['equipment_id']} "
                f"{det['parameter']} → critical in "
                f"~{rul.get('rul_point_estimate_hours', '?')} h",
                body=f"80% CI {rul.get('rul_ci80_hours', '?')}; "
                     f"{rul.get('caveat', '')}",
                ref=path.name))
        elif ev.get("dq_escalated"):
            notifications.append(notify(
                "DATA_QUALITY", ev["severity"],
                f"Dead/flatlined sensor: {ev['key']} — asset is "
                f"effectively unmonitored", ref=path.name))
        else:
            etype = ("ANOMALY_CRITICAL" if ev["severity"] == "CRITICAL"
                     else "ANOMALY_WARNING")
            notifications.append(notify(
                etype, ev["severity"],
                f"{ev['severity']} condition: {det['equipment_id']} "
                f"{det['parameter']} at {det['latest_value']} {det['unit']}",
                ref=path.name))

        # ── action: CRITICAL (reactive OR predictive) → work order ──
        if ev["severity"] == "CRITICAL":
            _raise_auto_wo(det, ev, path.name, work_orders, notifications)

    sla_breaches = _sla_watchdog(state, notifications)
    save_state(state)
    try:                                   # v11: bounded growth, every cycle
        pr = prune_retention()
        METRICS.inc("pruned_alerts_total", pr.get("pruned_alert_files", 0))
        METRICS.inc("pruned_notifications_total",
                    pr.get("pruned_notifications", 0))
    except Exception as exc:               # pruning must never kill autonomy
        METRICS.record_error(exc)
    summary = {"at": _now(), "streamed": streamed, "alerts": alerts,
               "resolved": resolved, "work_orders_raised": work_orders,
               "notifications": [n["event_type"] for n in notifications],
               "sla_breaches": sla_breaches}
    METRICS.record_cycle(summary)
    import logging as _logging
    log_event(get_logger("vulcan.autopilot"), _logging.INFO,
              "autonomous cycle complete",
              alerts=[a["key"] for a in alerts], resolved=resolved,
              work_orders=work_orders, sla_breaches=sla_breaches)
    return summary
