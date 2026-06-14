"""VULCAN Sentinel — autonomous plant monitoring (no human in the loop).

Closes the 'minimal human intervention' requirement: instead of waiting to
be asked, the sentinel watches every (equipment, parameter) pair on file —
on TWO channels. The reactive channel alerts when an anomaly layer fires;
the PREDICTIVE channel (v10) runs the RUL estimator every pass and alerts
when a failure is *projected* within the configured horizon, BEFORE any
limit is breached. When either channel changes for the worse it
autonomously:

  1. writes a structured abnormal-alert report to data/alerts/
  2. appends the event to the digital logbook
  3. (optional, --with-llm) invokes the full VULCAN agent to attach a
     complete diagnostic — anomaly evidence, RUL, risk, cited actions —
     so the engineer wakes up to a finished report, not a raw alarm.

State-aware de-duplication: severities are persisted per asset/parameter in
data/alerts/sentinel_state.json, so an unchanged WARNING is reported once,
not on every pass — escalations (WARNING→CRITICAL) always re-alert, and
recoveries are logged as RESOLVED. Honesty rules hold: the sentinel only
ever READS sensor data; it can never create it (same constraint as the UI).

Usage:
  python sentinel.py --once                # single pass (CI / cron friendly)
  python sentinel.py --watch 300           # daemon: re-scan every 300 s
  python sentinel.py --once --with-llm     # attach full agent diagnostics
Exit code (with --once): 0 = pass completed (alert count printed).
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from vulcan.config import DATA_DIR, rul_crit_hours, rul_warn_hours
from vulcan.tools.anomaly import scan_plant_health
from vulcan.tools.live import append_logbook
from vulcan.tools.rul import estimate_rul

ALERTS_DIR = DATA_DIR / "alerts"
STATE_PATH = ALERTS_DIR / "sentinel_state.json"
SEV_RANK = {"NORMAL": 0, "WARNING": 1, "CRITICAL": 2}


def _predictive_severity(rul: dict) -> str:
    """Map an estimate_rul result to an autonomous predictive severity.

    v10 stringency rules:
      * status OK (>=5 readings): RUL < crit horizon → CRITICAL,
        RUL < warn horizon → WARNING.
      * status INDICATIVE_ONLY (3-4 readings): capped at WARNING. An
        autonomous CRITICAL must never rest on a 3-point trend (C-07
        spirit: don't over-claim from thin evidence).
      * everything else (NOT_DEGRADING / INCALCULABLE) → NORMAL.
    """
    status = rul.get("status")
    if status not in ("OK", "INDICATIVE_ONLY"):
        return "NORMAL"
    point = rul.get("rul_point_estimate_hours")
    if point is None:
        return "NORMAL"
    if status == "OK" and point < rul_crit_hours():
        return "CRITICAL"
    if point < rul_warn_hours():
        return "WARNING"
    return "NORMAL"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _severity(detection: dict) -> str:
    sevs = {f["severity"] for f in detection.get("layers_fired", [])}
    if "CRITICAL" in sevs:
        return "CRITICAL"
    if "WARNING" in sevs:
        return "WARNING"
    return "NORMAL"


def load_state(path: Path | None = None) -> dict:
    """De-dup severity state, now on the SQLite WAL store (v11).

    v9 fixed call-time path resolution and atomic file writes; v11
    supersedes both: the state is a single transactional row in
    `data/vulcan.db` (key 'sentinel_state'), sharing the database's
    crash-safety and concurrency guarantees with the work-order ledger
    and notification feed. A legacy sentinel_state.json is auto-migrated
    on first open. The optional `path` argument is retained for
    compatibility: when given, it reads that JSON file (headless tooling)."""
    if path is not None:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}
    from vulcan import db
    raw = db.kv_get("sentinel_state", "")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def save_state(state: dict, path: Path | None = None) -> None:
    """Persist de-dup state transactionally (v11: one SQLite row — a crash
    or concurrent reader can never observe a torn state; the v8 failure
    mode of a corrupted file silently resetting all de-dup, i.e. an alarm
    storm, is structurally gone)."""
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(path)
        return
    from vulcan import db
    db.kv_set("sentinel_state", json.dumps(state))


def sentinel_pass(state: dict) -> tuple[list[dict], dict]:
    """One autonomous scan. Pure decision logic (no I/O) for testability.

    Returns (events, new_state). Each event is either a NEW/ESCALATED alert
    or a RESOLVED notice. Unchanged conditions produce NO event (dedup).

    v9 hardening: anomaly engine runs ONCE per pair (scan detections are
    reused); a flatlined series (FROZEN_VALUE) escalates to WARNING — a
    dead sensor means the asset is UNMONITORED.

    v10 — PREDICTIVE autonomy (FR5, unattended):
      Through v9 the sentinel was reactive: it alerted only AFTER a
      threshold or statistical layer had fired. v10 additionally runs the
      linear-drift RUL estimator on every pair, every pass. If the
      projected time-to-critical falls below the configured horizon, a
      PREDICTIVE alert fires — *before any limit is breached* — under its
      own de-dup key (`<pair>::RUL`), so reactive and predictive states
      escalate/resolve independently. Severity policy in
      `_predictive_severity` (an autonomous CRITICAL is never granted to a
      3-4 point trend).

    v10 — stale-state pruning:
      Keys for pairs that no longer exist in the data (decommissioned
      asset, renamed parameter) previously lived in sentinel_state.json
      forever. They are now dropped each pass. Bookkeeping keys
      (prefix '_', e.g. the SLA-breach ledger) are preserved.
    """
    events: list[dict] = []
    seen_keys: set[str] = set()
    new_state = {k: v for k, v in state.items() if k.startswith("_")}
    scan = scan_plant_health(include_detections=True)
    detections = scan.get("detections", {})
    for asset in scan.get("assets", []):
        key = f"{asset['equipment_id']}::{asset['parameter']}"
        det = detections.get(key)
        if not det or det.get("status") != "OK":
            continue

        # ── reactive channel (anomaly layers + data-quality escalation) ──
        sev = _severity(det)
        dq_escalated = False
        if sev == "NORMAL" and det.get("data_quality_flags"):
            sev, dq_escalated = "WARNING", True
        prev = state.get(key, "NORMAL")
        if SEV_RANK[sev] > SEV_RANK[prev]:
            events.append({"kind": "ALERT", "severity": sev,
                           "previous": prev, "key": key,
                           "detection": det, "health": asset,
                           "dq_escalated": dq_escalated})
        elif sev == "NORMAL" and prev != "NORMAL":
            events.append({"kind": "RESOLVED", "severity": sev,
                           "previous": prev, "key": key,
                           "detection": det, "health": asset})
        seen_keys.add(key)
        new_state[key] = sev

        # ── predictive channel (v10): RUL horizon, own de-dup key ──
        pkey = f"{key}::RUL"
        rul = estimate_rul(asset["equipment_id"], asset["parameter"])
        psev = _predictive_severity(rul)
        pprev = state.get(pkey, "NORMAL")
        if SEV_RANK[psev] > SEV_RANK[pprev]:
            events.append({"kind": "ALERT", "severity": psev,
                           "previous": pprev, "key": pkey,
                           "predictive": True, "rul": rul,
                           "detection": det, "health": asset})
        elif psev == "NORMAL" and pprev != "NORMAL":
            events.append({"kind": "RESOLVED", "severity": psev,
                           "previous": pprev, "key": pkey,
                           "predictive": True, "rul": rul,
                           "detection": det, "health": asset})
        seen_keys.add(pkey)
        new_state[pkey] = psev
    return events, new_state


def render_alert_md(ev: dict, llm_section: str = "") -> str:
    det, health = ev["detection"], ev["health"]
    th = det.get("thresholds") or {}
    predictive = bool(ev.get("predictive"))
    head = ("VULCAN SENTINEL PREDICTIVE FAILURE ALERT" if predictive
            else "VULCAN SENTINEL ABNORMAL ALERT")
    lines = [
        f"# {'🚨' if ev['severity'] == 'CRITICAL' else '⚠️'} {head} — "
        f"{ev['severity']}",
        "",
        f"- **Generated (autonomous):** {_now()}",
        f"- **Asset / parameter:** {det['equipment_id']} / {det['parameter']}",
        f"- **Latest reading:** {det['latest_value']} {det['unit']} "
        f"(at {det['last_timestamp']})",
        f"- **Limits:** warning {th.get('warning', 'n/a')} / "
        f"critical {th.get('critical', 'n/a')} {th.get('unit', '')}",
        f"- **Severity transition:** {ev['previous']} → {ev['severity']}"
        + (" *(escalated: data-quality — asset is effectively unmonitored)*"
           if ev.get("dq_escalated") else ""),
        f"- **Health score:** {health['health_score']}/100 "
        f"({health['status']})",
    ]
    if predictive:
        rul = ev.get("rul", {})
        lines += [
            "",
            "## Predictive evidence (v10 — fired BEFORE limit breach)",
            f"- **Model:** {rul.get('model', 'linear_regression_drift')}",
            f"- **Projected time to critical limit:** "
            f"{rul.get('rul_point_estimate_hours', '?')} h "
            f"(80% CI {rul.get('rul_ci80_hours', '?')})",
            f"- **Degradation rate:** "
            f"{rul.get('degradation_rate_per_hour', '?')} "
            f"{det['unit']}/h over {rul.get('n_readings', '?')} readings "
            f"(R² {rul.get('r_squared', '?')})",
            f"- **Caveat:** {rul.get('caveat', '')}",
            "",
            "*No limit has been breached yet — this alert exists so the "
            "intervention can be PLANNED instead of forced.*",
        ]
    lines.append("\n## Anomaly layers fired (Tier-1 evidence)")
    fired = det.get("layers_fired", [])
    if fired:
        for f in fired:
            lines.append(f"- `{f['layer']}` [{f['severity']}] — {f['detail']}")
    else:
        lines.append("- *(none — condition is currently within limits; "
                     "see predictive evidence above)*")
    if det.get("data_quality_flags"):
        lines.append("\n## Data-quality flags")
        lines += [f"- {q}" for q in det["data_quality_flags"]]
    lines.append("\n*Source: stored sensor readings only — the sentinel "
                 "reads data, never creates it (C-07).*")
    if llm_section:
        lines += ["", "## Autonomous VULCAN diagnostic", "", llm_section]
    return "\n".join(lines) + "\n"


def write_alert(ev: dict, llm_section: str = "") -> Path:
    ALERTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe = ev["key"].replace("::", "_").replace("/", "-")
    prefix = "PREDICT" if ev.get("predictive") else "ALERT"
    out = ALERTS_DIR / f"{prefix}_{stamp}_{safe}.md"
    # v10: atomic tmp+rename, same crash-safety policy as state/ledger —
    # a reader (UI inbox, daemon LLM annotator) can never see a half file.
    tmp = out.with_suffix(".md.tmp")
    tmp.write_text(render_alert_md(ev, llm_section), encoding="utf-8")
    tmp.replace(out)
    return out


def run_once(with_llm: bool = False) -> int:
    state = load_state()
    events, state = sentinel_pass(state)
    n_alerts = 0
    for ev in events:
        if ev["kind"] == "RESOLVED":
            append_logbook(f"[SENTINEL] RESOLVED — {ev['key']} returned to "
                           f"NORMAL (was {ev['previous']}). {_now()}")
            print(f"[sentinel] RESOLVED {ev['key']}")
            continue
        llm_section = ""
        if with_llm:
            try:  # full agent diagnostic, fully unattended
                from vulcan.orchestrator import VulcanOrchestrator
                det = ev["detection"]
                llm_section = VulcanOrchestrator().ask(
                    f"AUTONOMOUS SENTINEL ALERT: {det['equipment_id']} "
                    f"{det['parameter']} latest {det['latest_value']} "
                    f"{det['unit']}; layers fired: "
                    f"{[f['layer'] for f in det['layers_fired']]}. "
                    "Produce the Section-10 real-time alert block plus a "
                    "full diagnostic with RUL, risk and prioritized actions.")
            except Exception as exc:
                llm_section = (f"*(LLM diagnostic unavailable: {exc} — "
                               "deterministic evidence above stands alone.)*")
        path = write_alert(ev, llm_section)
        append_logbook(f"[SENTINEL] {ev['severity']} alert on {ev['key']} "
                       f"→ {path.name}")
        print(f"[sentinel] {ev['severity']} {ev['key']} → {path}")
        n_alerts += 1
    save_state(state)
    print(f"[sentinel] pass complete — {n_alerts} new alert(s), "
          f"{len(events) - n_alerts} resolution(s), state persisted.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="VULCAN autonomous sentinel")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--once", action="store_true", help="single pass and exit")
    g.add_argument("--watch", type=int, metavar="SECONDS",
                   help="daemon mode: re-scan every N seconds")
    ap.add_argument("--with-llm", action="store_true",
                    help="attach a full VULCAN agent diagnostic to each "
                         "alert (needs ANTHROPIC_API_KEY)")
    args = ap.parse_args()
    if args.once:
        return run_once(with_llm=args.with_llm)
    print(f"[sentinel] watching — interval {args.watch}s (Ctrl-C to stop)")
    while True:
        run_once(with_llm=args.with_llm)
        time.sleep(max(5, args.watch))


if __name__ == "__main__":
    raise SystemExit(main())
