"""Pytest wrapper around the evaluation harness + targeted unit tests."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_full_eval_harness_passes():
    from evals.run_evals import main
    assert main() == 0, "evaluation harness reported failures"


def test_orchestrator_registry_consistent():
    import vulcan.orchestrator as o
    assert set(s["name"] for s in o.TOOL_SCHEMAS) == set(o.TOOL_IMPLS)
    assert len(o.TOOL_SCHEMAS) == 15  # v8: + work-order create/list/update


def test_prompt_cache_prefix_is_stable():
    # Cache breakpoint must sit on the static prompt (block 0); the volatile
    # timestamped runtime context must come AFTER it, or every API call is a
    # guaranteed cache miss on the 50KB+ system prompt.
    import vulcan.orchestrator as o
    sys_blocks = o._build_system()
    assert "cache_control" in sys_blocks[0]
    assert "cache_control" not in sys_blocks[1]
    assert "HOST RUNTIME CONTEXT" in sys_blocks[1]["text"]
    # the cached block must be byte-identical across builds
    assert o._build_system()[0]["text"] == sys_blocks[0]["text"]


def test_shared_spare_maps_to_all_compatible_assets():
    # ';'-separated compatible_equipment must match by membership.
    from vulcan.tools.priority import _spares_exposure
    _, detail = _spares_exposure("UTIL-HPU-03")
    assert detail.get("flag") != "NO_COMPATIBLE_SPARE_LISTED"


def test_arrhenius_rule_of_thumb():
    # ~2x acceleration per 10degC at Ea~0.7eV near 70-90degC
    from vulcan.tools.rul import estimate_rul_arrhenius
    r = estimate_rul_arrhenius(0.7, 70, 80, 10000, 0)
    assert 1.6 < r["acceleration_factor_vs_design"] < 2.4


def test_pf_window_expiry():
    from vulcan.tools.rul import estimate_rul_pf_interval
    assert estimate_rul_pf_interval(100, 150)["status"] == "WINDOW_EXPIRED"


def test_hybrid_search_returns_provenance():
    from vulcan.tools.retrieval import search_knowledge_base
    q = search_knowledge_base("bearing lubrication seal")
    assert q["n_results"] > 0
    top = q["results"][0]
    for key in ("doc_name", "chunk_id", "doc_type", "fusion_score"):
        assert key in top


def test_prioritizer_section_5_2_criteria():
    from vulcan.tools.priority import rank_maintenance_priorities, WEIGHTS
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9
    r = rank_maintenance_priorities()
    assert r["status"] == "OK"
    needed = {"health_risk", "delay_severity", "criticality",
              "spares_exposure"}
    for a in r["ranking"]:
        assert needed <= set(a["factors"])
        assert a["rank"] >= 1


def test_prioritizer_flags_unmapped_criticality():
    # Asset present only in the delay log must get a flagged default,
    # never a silently invented criticality.
    from vulcan.tools.priority import rank_maintenance_priorities
    r = rank_maintenance_priorities()
    for a in r["ranking"]:
        src = a["factors"]["criticality"]["source"]
        flagged = any("CRITICALITY_NOT_MAPPED" in f for f in a["flags"])
        assert flagged == src.startswith("DEFAULT")


def test_sentinel_autonomous_and_deduplicated():
    from sentinel import sentinel_pass
    ev1, st1 = sentinel_pass({})
    assert any(e["kind"] == "ALERT" and e["key"] == "CC2-MO-01::vibration_mm_s"
               for e in ev1)
    ev2, _ = sentinel_pass(st1)
    assert not [e for e in ev2 if e["kind"] == "ALERT"]


# ───────────────────────── v8 upgrade tests ─────────────────────────

def test_datastore_cache_hits_and_invalidates(tmp_path, monkeypatch):
    # Same fingerprint -> same cached object (no disk re-read);
    # touching a file -> fresh object.
    import importlib
    import time

    import vulcan.datastore as ds
    df1 = ds.get_all_readings()
    df2 = ds.get_all_readings()
    assert df1 is df2, "cache must serve the same object while files unchanged"

    from vulcan.config import SENSOR_DATA_DIR
    target = SENSOR_DATA_DIR / "readings.csv"
    old = target.stat().st_mtime_ns
    import os
    os.utime(target, ns=(old + 1_000_000, old + 1_000_000))
    df3 = ds.get_all_readings()
    assert df3 is not df1, "mtime change must invalidate the cache"


def test_fleet_scan_uses_single_cached_load(monkeypatch):
    # scan_plant_health must not re-read CSVs per pair: pandas.read_csv
    # may be called at most once per file across an entire scan.
    import pandas as pd

    import vulcan.datastore as ds
    from vulcan.tools.anomaly import scan_plant_health

    ds.invalidate()
    calls = {"n": 0}
    real = pd.read_csv

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(pd, "read_csv", counting)
    scan = scan_plant_health()
    assert scan["n_assets_scanned"] >= 3
    n_files = len(list(ds.SENSOR_DATA_DIR.glob("*.csv")))
    assert calls["n"] <= n_files, (
        f"scan triggered {calls['n']} disk reads for {n_files} files — "
        "per-pair re-reading has regressed")


def test_work_order_dedup_and_lifecycle(tmp_path, monkeypatch):
    import vulcan.tools.workorders as wo
    r1 = wo.create_work_order("CC2-MO-01", "Inspect bearing",
                              priority="CRITICAL",
                              parameter="vibration_mm_s", source="test")
    assert r1["status"] == "CREATED"
    r2 = wo.create_work_order("CC2-MO-01", "Inspect bearing again",
                              priority="HIGH",
                              parameter="vibration_mm_s", source="test")
    assert r2["status"] == "DUPLICATE_OPEN", "open WO must de-duplicate"
    wid = r1["work_order"]["id"]
    assert wo.update_work_order(wid, "DONE")["status"] == "UPDATED"
    r3 = wo.create_work_order("CC2-MO-01", "Inspect bearing post-fix",
                              parameter="vibration_mm_s", source="test")
    assert r3["status"] == "CREATED", "closing the WO must allow a new one"


def test_learning_priors_block_aggregates(monkeypatch, tmp_path):
    import vulcan.learning as lr
    import vulcan.tools.cmms as cm
    db = tmp_path / "fb.sqlite3"
    monkeypatch.setattr(cm, "FEEDBACK_DB_PATH", db)
    monkeypatch.setattr(lr, "FEEDBACK_DB_PATH", db)
    assert lr.learning_priors_block() == ""  # empty store -> zero overhead
    cm.record_feedback("R1", "mold_oscillator", "bearing_wear", "CONFIRMED")
    cm.record_feedback("R2", "mold_oscillator", "bearing_wear", "CONFIRMED")
    block = lr.learning_priors_block()
    assert "mold_oscillator" in block and "CONFIRMED x2" in block
    assert "raise confidence" in block


def test_history_compaction_replaces_old_tool_results(monkeypatch):
    # Old bulky tool_result payloads must shrink to stubs; recent ones and
    # assistant prose must survive untouched.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from vulcan.orchestrator import VulcanOrchestrator, _compacted_stub
    orch = VulcanOrchestrator()
    big = '{"status": "OK", "blob": "' + "x" * 2000 + '"}'
    for turn in range(4):
        orch._turn_index.append(len(orch.messages))
        orch.messages += [
            {"role": "user", "content": f"q{turn}"},
            {"role": "assistant", "content": [{"type": "text",
                                               "text": f"a{turn}"}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": f"t{turn}",
                 "content": big}]},
        ]
    orch._compact_history()
    old_result = orch.messages[2]["content"][0]["content"]
    new_result = orch.messages[-1]["content"][0]["content"]
    assert '"compacted": true' in old_result
    assert new_result == big, "recent turns must never be compacted"
    assert len(_compacted_stub(big)) < 200


def test_autopilot_tick_alerts_and_raises_critical_wo(tmp_path, monkeypatch):
    # A CRITICAL detection in a tick must yield: alert file + work order.
    import sentinel as sn
    import vulcan.autopilot as ap
    import vulcan.notify as nt
    import vulcan.tools.live as live
    import vulcan.tools.workorders as wo
    monkeypatch.setattr(sn, "ALERTS_DIR", tmp_path / "alerts")
    monkeypatch.setattr(live, "LOGBOOK_PATH", tmp_path / "logbook.md")
    summary = ap.autopilot_tick(auto_stream=False)
    assert summary["alerts"], "seeded degradation must alert on first tick"
    crit = [a for a in summary["alerts"] if a["severity"] == "CRITICAL"]
    if crit:  # seeded data trips CRITICAL on bearing temp
        assert summary["work_orders_raised"], \
            "CRITICAL alert must auto-raise a work order"
    # Second tick: nothing changed -> de-dup, no repeat alerts.
    summary2 = ap.autopilot_tick(auto_stream=False)
    assert not summary2["alerts"], "unchanged state must not re-alert"


# ───────────────────────── v9 hardening tests ─────────────────────────

def test_streaming_is_genuine_not_rechunked(monkeypatch):
    # The v8 regression: ask_stream sliced a finished string into 80-char
    # chunks. v9 must call the REAL streaming API (client.messages.stream).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    import vulcan.orchestrator as o
    orch = o.VulcanOrchestrator()
    assert not hasattr(orch, "_chunk"), "no re-chunking helper expected"
    import inspect
    src = inspect.getsource(o.VulcanOrchestrator._stream_round)
    assert "messages.stream" in src, "must use the streaming API"
    src_ask = inspect.getsource(o.VulcanOrchestrator.ask_stream)
    assert "text[i:i + step]" not in src_ask, \
        "cosmetic 80-char re-chunking has regressed"


def test_sentinel_runs_anomaly_engine_once_per_pair(monkeypatch):
    # v8 ran detect_anomalies twice per pair per pass (once in the scan,
    # once again in sentinel_pass). v9 must reuse the scan's detections.
    import sentinel as sn
    import vulcan.tools.anomaly as an
    calls = {"n": 0}
    real = an.detect_anomalies

    def counting(eq, p):
        calls["n"] += 1
        return real(eq, p)

    monkeypatch.setattr(an, "detect_anomalies", counting)
    from vulcan import datastore as ds
    n_pairs = len(ds.list_pairs())
    sn.sentinel_pass({})
    assert calls["n"] <= n_pairs, (
        f"{calls['n']} detections for {n_pairs} pairs — "
        "double computation has regressed")


def test_dead_sensor_escalates_to_autonomous_warning():
    # A flatlined (FROZEN_VALUE) series means the asset is unmonitored —
    # the sentinel must alert on it, not silently compute-and-ignore.
    from sentinel import sentinel_pass
    ev, _ = sentinel_pass({})
    frozen = [e for e in ev if e["key"] == "BF3-GCP-FAN-02::motor_current_A"]
    assert frozen and frozen[0]["severity"] == "WARNING"
    assert frozen[0].get("dq_escalated") is True


def test_work_order_priority_escalates_not_swallowed(tmp_path, monkeypatch):
    import vulcan.tools.workorders as wo
    r1 = wo.create_work_order("CC2-MO-01", "Inspect", priority="MEDIUM",
                              parameter="vibration_mm_s", source="engineer")
    assert r1["status"] == "CREATED"
    r2 = wo.create_work_order("CC2-MO-01", "Investigate CRITICAL",
                              priority="CRITICAL",
                              parameter="vibration_mm_s", source="autopilot")
    assert r2["status"] == "ESCALATED"
    assert r2["work_order"]["priority"] == "CRITICAL"
    assert "escalation_note" in r2["work_order"]
    # lower/equal priority still de-duplicates quietly
    r3 = wo.create_work_order("CC2-MO-01", "again", priority="LOW",
                              parameter="vibration_mm_s")
    assert r3["status"] == "DUPLICATE_OPEN"


def test_learning_prior_requires_minimum_evidence(monkeypatch, tmp_path):
    import vulcan.learning as lr
    import vulcan.tools.cmms as cm
    db = tmp_path / "fb.sqlite3"
    monkeypatch.setattr(cm, "FEEDBACK_DB_PATH", db)
    monkeypatch.setattr(lr, "FEEDBACK_DB_PATH", db)
    cm.record_feedback("R1", "pump", "seal_leak", "CONFIRMED")
    block = lr.learning_priors_block()
    assert "insufficient evidence (n=1)" in block
    assert "raise confidence" not in block
    cm.record_feedback("R2", "pump", "seal_leak", "CONFIRMED")
    assert "raise confidence" in lr.learning_priors_block()


def test_autopilot_tick_is_single_flight():
    import threading

    import vulcan.autopilot as ap
    # hold the lock as if a tick is mid-flight; a second caller must skip
    assert ap._tick_lock.acquire(blocking=False)
    try:
        s = ap.autopilot_tick()
        assert s.get("skipped"), "overlapping ticks must be skipped"
    finally:
        ap._tick_lock.release()


def test_sentinel_state_save_is_atomic(tmp_path):
    from sentinel import load_state, save_state
    p = tmp_path / "state.json"
    save_state({"a": "WARNING"}, p)
    assert load_state(p) == {"a": "WARNING"}
    assert not p.with_suffix(".json.tmp").exists(), "tmp file must be renamed"


def test_daemon_lifecycle(monkeypatch):
    import vulcan.daemon as dm
    ticks = {"n": 0}
    monkeypatch.setattr(dm, "autopilot_tick",
                        lambda auto_stream=False: (ticks.__setitem__(
                            "n", ticks["n"] + 1) or
                            {"at": "t", "alerts": [], "resolved": [],
                             "streamed": [], "work_orders_raised": []}))
    d = dm.SentinelDaemon()
    assert d.start(interval=5) is True
    assert d.start(interval=5) is False, "start must be idempotent"
    import time
    time.sleep(0.3)
    d.stop()
    time.sleep(0.1)
    assert not d.running
    assert ticks["n"] >= 1, "daemon must tick with no UI session attached"


def test_sentinel_state_resolves_db_at_call_time(tmp_path, monkeypatch):
    # v9 fixed call-time FILE path resolution; v11 supersedes it: state
    # lives in the SQLite store, resolved from VULCAN_DB_PATH at call
    # time, so tests/operators can repoint without a restart and a test
    # run can never leak state into the repo database.
    import sentinel as sn
    monkeypatch.setenv("VULCAN_DB_PATH", str(tmp_path / "a.db"))
    sn.save_state({"k": "WARNING"})
    assert sn.load_state() == {"k": "WARNING"}
    assert (tmp_path / "a.db").exists()
    monkeypatch.setenv("VULCAN_DB_PATH", str(tmp_path / "b.db"))
    assert sn.load_state() == {}, "fresh DB must mean fresh state"


# ───────────────────────── v10 stringency tests ─────────────────────────

def _synthetic_sensor_dir(tmp_path, n_points, slope_per_h, start, warn,
                          crit, eq="TEST-EQ-01", param="temp_C"):
    """Build an isolated sensor universe: a rising series + thresholds."""
    from datetime import datetime, timedelta
    d = tmp_path / "sensors"
    d.mkdir()
    t0 = datetime(2026, 6, 1, 0, 0)
    rows = ["timestamp,equipment_id,parameter,value,unit"]
    for i in range(n_points):
        ts = t0 + timedelta(hours=4 * i)
        rows.append(f"{ts:%Y-%m-%d %H:%M:%S},{eq},{param},"
                    f"{start + slope_per_h * 4 * i:.2f},degC")
    (d / "readings.csv").write_text("\n".join(rows) + "\n")
    (d / "thresholds.csv").write_text(
        "equipment_id,parameter,warning,critical,unit\n"
        f"{eq},{param},{warn},{crit},degC\n")
    return d


def _isolate_sensors(monkeypatch, sensor_dir):
    import vulcan.datastore as ds
    monkeypatch.setattr(ds, "SENSOR_DATA_DIR", sensor_dir)
    ds.invalidate()
    return ds


def test_predictive_alert_fires_before_any_limit_breach(tmp_path,
                                                        monkeypatch):
    # latest value BELOW warning, but trend crosses critical within the
    # horizon -> autonomous PREDICTIVE alert must fire (FR5, unattended).
    d = _synthetic_sensor_dir(tmp_path, n_points=10, slope_per_h=0.5,
                              start=50.0, warn=80.0, crit=90.0)
    ds = _isolate_sensors(monkeypatch, d)
    try:
        from sentinel import sentinel_pass
        ev, st = sentinel_pass({})
        latest = 50.0 + 0.5 * 4 * 9          # = 68 < warning 80
        assert latest < 80.0
        pred = [e for e in ev if e.get("predictive") and e["kind"] == "ALERT"]
        assert pred, "predictive channel must fire before limit breach"
        p = pred[0]
        assert p["key"].endswith("::RUL")
        assert p["rul"]["rul_point_estimate_hours"] < 72
        # dedup: unchanged second pass -> no repeat predictive alert
        ev2, _ = sentinel_pass(st)
        assert not [e for e in ev2 if e.get("predictive")
                    and e["kind"] == "ALERT"]
    finally:
        ds.invalidate()


def test_predictive_critical_requires_full_evidence(tmp_path, monkeypatch):
    # 4 readings (INDICATIVE_ONLY) crossing critical in <24h must be capped
    # at WARNING: an autonomous CRITICAL never rests on a 3-4 point trend.
    d = _synthetic_sensor_dir(tmp_path, n_points=4, slope_per_h=2.0,
                              start=60.0, warn=85.0, crit=95.0)
    ds = _isolate_sensors(monkeypatch, d)
    try:
        from sentinel import _predictive_severity, sentinel_pass
        from vulcan.tools.rul import estimate_rul
        r = estimate_rul("TEST-EQ-01", "temp_C")
        assert r["status"] == "INDICATIVE_ONLY"
        assert r["rul_point_estimate_hours"] < 24
        assert _predictive_severity(r) == "WARNING", \
            "thin-evidence trend must never drive an autonomous CRITICAL"
        ev, _ = sentinel_pass({})
        pred = [e for e in ev if e.get("predictive") and e["kind"] == "ALERT"]
        assert pred and pred[0]["severity"] == "WARNING"
    finally:
        ds.invalidate()


def test_stale_state_keys_are_pruned_but_bookkeeping_survives():
    from sentinel import sentinel_pass
    state = {"GHOST-EQ::dead_param": "CRITICAL",          # decommissioned
             "_sla_notified": ["WO-X"]}                   # bookkeeping
    _, new_state = sentinel_pass(state)
    assert "GHOST-EQ::dead_param" not in new_state, \
        "stale pair keys must be pruned each pass"
    assert new_state.get("_sla_notified") == ["WO-X"], \
        "'_'-prefixed bookkeeping keys must be preserved"


def test_autopilot_routes_role_notifications(tmp_path, monkeypatch):
    import sentinel as sn
    import vulcan.autopilot as ap
    import vulcan.notify as nt
    import vulcan.tools.live as live
    import vulcan.tools.workorders as wo
    monkeypatch.setattr(sn, "ALERTS_DIR", tmp_path / "alerts")
    monkeypatch.setattr(live, "LOGBOOK_PATH", tmp_path / "logbook.md")
    summary = ap.autopilot_tick(auto_stream=False)
    assert summary["alerts"], "seeded data must alert on a cold tick"
    assert summary["notifications"], "every alert must route a notification"
    # the seeded predictive CRITICAL must reach the planner role (FR7)
    planner = nt.read_notifications(role="planner")
    assert any(n["event_type"] == "PREDICTIVE_FAILURE" for n in planner)
    # zero-stock spare on the predicted-critical asset -> procurement risk
    proc = nt.read_notifications(role="procurement")
    assert any(n["event_type"] == "PROCUREMENT_RISK" for n in proc)


def test_predictive_critical_auto_raises_spares_checked_wo(tmp_path,
                                                           monkeypatch):
    import json
    import sentinel as sn
    import vulcan.autopilot as ap
    import vulcan.notify as nt
    import vulcan.tools.live as live
    import vulcan.tools.workorders as wo
    monkeypatch.setattr(sn, "ALERTS_DIR", tmp_path / "alerts")
    monkeypatch.setattr(live, "LOGBOOK_PATH", tmp_path / "logbook.md")
    ap.autopilot_tick(auto_stream=False)
    orders = wo.list_work_orders()["work_orders"]
    lf = [w for w in orders if w["equipment_id"] == "LF1-HYD-01"]
    assert lf, "predictive CRITICAL must auto-raise a work order " \
               "(intervention planned BEFORE failure)"
    assert lf[0]["priority"] == "CRITICAL"
    assert "PREDICTIVE" in lf[0]["details"]
    assert "Spares (live CMMS)" in lf[0]["details"], \
        "auto work orders must carry the live spares check (Section 5.2)"


def test_wo_sla_breach_notifies_exactly_once(tmp_path, monkeypatch):
    import json
    import sentinel as sn
    import vulcan.autopilot as ap
    import vulcan.notify as nt
    import vulcan.tools.live as live
    import vulcan.tools.workorders as wo
    monkeypatch.setattr(sn, "ALERTS_DIR", tmp_path / "alerts")
    monkeypatch.setattr(live, "LOGBOOK_PATH", tmp_path / "logbook.md")
    # an OPEN CRITICAL order created 3h ago (SLA default 60 min)
    from vulcan import db
    db.wo_insert({
        "id": "WO-OLD-0001", "created_at": "2026-06-12T00:00:00+00:00",
        "equipment_id": "CC2-MO-01", "parameter": "vibration_mm_s",
        "title": "old critical", "priority": "CRITICAL", "status": "OPEN",
        "details": "", "source": "autopilot", "evidence_ref": ""})
    s1 = ap.autopilot_tick(auto_stream=False)
    assert "WO-OLD-0001" in s1["sla_breaches"], \
        "stale CRITICAL order must trigger an SLA-breach escalation"
    s2 = ap.autopilot_tick(auto_stream=False)
    assert "WO-OLD-0001" not in s2["sla_breaches"], \
        "SLA breach must escalate exactly once (de-dup ledger)"
    sup = nt.read_notifications(role="supervisor")
    assert sum(1 for n in sup
               if n["event_type"] == "WORK_ORDER_SLA_BREACH") == 1


def test_notification_router_persists_and_filters_by_role():
    from vulcan.notify import notify, read_notifications
    notify("ANOMALY_CRITICAL", "CRITICAL", "t1", ref="A.md")
    notify("PROCUREMENT_RISK", "HIGH", "t2", ref="B.md")
    eng = read_notifications(role="engineer")
    proc = read_notifications(role="procurement")
    assert [n["title"] for n in eng] == ["t1"]
    assert [n["title"] for n in proc] == ["t2"]
    assert all("webhook" in n for n in eng + proc)


def test_daemon_autostart_honors_env(monkeypatch):
    import vulcan.daemon as dm
    fresh = dm.SentinelDaemon()
    monkeypatch.setattr(dm, "get_daemon", lambda: fresh)
    # stub the tick: this test is about LIFECYCLE, not plant I/O
    monkeypatch.setattr(dm, "autopilot_tick", lambda **k: {
        "at": "t", "streamed": [], "alerts": [], "resolved": [],
        "work_orders_raised": [], "notifications": [], "sla_breaches": []})
    # opt-out respected
    monkeypatch.setenv("VULCAN_DAEMON_AUTOSTART", "0")
    d = dm.ensure_autostarted()
    assert not d.running, "AUTOSTART=0 must keep the daemon stopped"
    # default ON: autonomy with zero clicks
    monkeypatch.setenv("VULCAN_DAEMON_AUTOSTART", "1")
    monkeypatch.setenv("VULCAN_DAEMON_INTERVAL", "3600")
    d = dm.ensure_autostarted()
    try:
        assert d.running and d.autostarted, \
            "autonomy must be the default, not an opt-in"
        # idempotent across reruns
        assert dm.ensure_autostarted() is d and d.running
    finally:
        d.stop()


def test_alert_write_is_atomic_and_predictive_named(tmp_path, monkeypatch):
    import sentinel as sn
    monkeypatch.setattr(sn, "ALERTS_DIR", tmp_path)
    from sentinel import sentinel_pass, write_alert
    ev, _ = sentinel_pass({})
    pred = next(e for e in ev if e.get("predictive") and e["kind"] == "ALERT")
    path = write_alert(pred)
    assert path.name.startswith("PREDICT_")
    assert not list(tmp_path.glob("*.tmp")), "tmp file must be renamed away"
    assert "PREDICTIVE FAILURE ALERT" in path.read_text(encoding="utf-8")


# ───────────────────────── v11 production tests ─────────────────────────

def test_db_is_wal_and_migrates_legacy_files(tmp_path, monkeypatch):
    import json as _json
    monkeypatch.setenv("VULCAN_DB_PATH", str(tmp_path / "plant.db"))
    # legacy v10 artifacts sitting next to the new database
    (tmp_path / "work_orders.json").write_text(_json.dumps([{
        "id": "WO-LEGACY-0001", "created_at": "2026-06-01T00:00:00+00:00",
        "equipment_id": "CC2-MO-01", "parameter": "vibration_mm_s",
        "title": "legacy order", "priority": "HIGH", "status": "OPEN",
        "details": "", "source": "agent", "evidence_ref": ""}]))
    (tmp_path / "notifications.jsonl").write_text(_json.dumps({
        "at": "2026-06-01T00:00:00+00:00", "event_type": "ANOMALY_WARNING",
        "severity": "WARNING", "roles": ["engineer"], "title": "legacy n",
        "body": "", "ref": "", "webhook": "disabled"}) + "\n")
    from vulcan import db
    with __import__("contextlib").closing(db.connect()) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal", \
            "production store must run in WAL mode"
    from vulcan.tools.workorders import list_work_orders
    ids = [w["id"] for w in list_work_orders()["work_orders"]]
    assert "WO-LEGACY-0001" in ids, "v10 ledger must migrate losslessly"
    from vulcan.notify import read_notifications
    assert any(n["title"] == "legacy n" for n in read_notifications())
    assert (tmp_path / "work_orders.json.migrated").exists(), \
        "migrated legacy file must be renamed, not silently kept live"


def test_alert_storm_rolls_up_instead_of_flooding(tmp_path, monkeypatch):
    # 30 synthetic pairs all breaching at once (> default cap 25): the
    # cycle must emit ONE roll-up, not 30 files + 30 notifications + WOs.
    from datetime import datetime, timedelta
    d = tmp_path / "sensors"
    d.mkdir()
    t0 = datetime(2026, 6, 1, 0, 0)
    rows = ["timestamp,equipment_id,parameter,value,unit"]
    th = ["equipment_id,parameter,warning,critical,unit"]
    for n in range(30):
        eq = f"STORM-EQ-{n:02d}"
        for i in range(8):
            ts = t0 + timedelta(hours=4 * i)
            rows.append(f"{ts:%Y-%m-%d %H:%M:%S},{eq},temp_C,"
                        f"{50 + 8 * i},degC")        # latest 106 > crit
        th.append(f"{eq},temp_C,80,100,degC")
    (d / "readings.csv").write_text("\n".join(rows) + "\n")
    (d / "thresholds.csv").write_text("\n".join(th) + "\n")
    ds = _isolate_sensors(monkeypatch, d)
    import sentinel as sn
    import vulcan.autopilot as ap
    import vulcan.tools.live as live
    monkeypatch.setattr(sn, "ALERTS_DIR", tmp_path / "alerts")
    monkeypatch.setattr(live, "LOGBOOK_PATH", tmp_path / "logbook.md")
    try:
        s = ap.autopilot_tick(auto_stream=False)
        assert s.get("alert_storm", 0) > 25
        assert len(s["alerts"]) == 1
        assert s["alerts"][0]["key"] == "STORM_ROLLUP"
        assert s["work_orders_raised"] == [], \
            "a storm must not auto-raise dozens of work orders"
        files = list((tmp_path / "alerts").glob("*.md"))
        assert len(files) == 1 and "STORM_ROLLUP" in files[0].name
        body = files[0].read_text(encoding="utf-8")
        assert "SYSTEMIC" in body and "STORM-EQ-00::temp_C" in body
    finally:
        ds.invalidate()


def test_retention_prunes_old_artifacts_but_never_work_orders(
        tmp_path, monkeypatch):
    import os
    import time as _t
    from vulcan import db
    from vulcan.notify import notify, read_notifications
    from vulcan.retention import prune_retention
    from vulcan.tools.workorders import create_work_order, list_work_orders
    alerts = tmp_path / "alerts"
    alerts.mkdir()
    old_f = alerts / "ALERT_old.md"
    old_f.write_text("old")
    os.utime(old_f, (_t.time() - 90 * 86400,) * 2)        # 90 days old
    new_f = alerts / "PREDICT_new.md"
    new_f.write_text("new")
    notify("ANOMALY_WARNING", "WARNING", "ancient",
           roles=["engineer"])
    # backdate the row
    with __import__("contextlib").closing(db.connect()) as conn:
        conn.execute("UPDATE notifications SET at='2020-01-01T00:00:00'")
        conn.commit()
    notify("ANOMALY_WARNING", "WARNING", "fresh", roles=["engineer"])
    create_work_order("OLD-EQ", "ancient order")          # never pruned
    res = prune_retention(alerts_dir=alerts)
    assert res["pruned_alert_files"] == 1
    assert not old_f.exists() and new_f.exists()
    titles = [n["title"] for n in read_notifications()]
    assert "fresh" in titles and "ancient" not in titles
    assert list_work_orders()["n"] == 1, \
        "work orders are the audit trail — retention must never touch them"


def test_retention_zero_disables(monkeypatch, tmp_path):
    monkeypatch.setenv("VULCAN_RETENTION_DAYS", "0")
    from vulcan.retention import prune_retention
    assert prune_retention(alerts_dir=tmp_path).get("disabled") is True


def test_webhook_hmac_signature_is_stable_and_secret_bound():
    from vulcan.notify import sign_payload
    sig = sign_payload(b'{"a":1}', "topsecret")
    assert sig.startswith("sha256=") and len(sig) == 7 + 64
    assert sig == sign_payload(b'{"a":1}', "topsecret"), "deterministic"
    assert sig != sign_payload(b'{"a":1}', "other"), "secret-bound"
    assert sig != sign_payload(b'{"a":2}', "topsecret"), "payload-bound"


def test_config_validation_fails_fast_on_nonsense(monkeypatch):
    from vulcan.config import validate_config
    assert validate_config() == [], "shipped defaults must validate clean"
    monkeypatch.setenv("VULCAN_RUL_WARN_HOURS", "10")     # warn < crit!
    monkeypatch.setenv("VULCAN_RUL_CRIT_HOURS", "24")
    errs = validate_config()
    assert any("CRIT" in e for e in errs), \
        "inverted RUL horizons must refuse to start"
    monkeypatch.setenv("VULCAN_RUL_WARN_HOURS", "72")
    monkeypatch.setenv("VULCAN_RUL_CRIT_HOURS", "24")
    monkeypatch.setenv("VULCAN_DAEMON_INTERVAL", "1")
    assert any("INTERVAL" in e for e in validate_config())


def test_service_health_endpoint_serves_metrics(monkeypatch):
    import json as _json
    import urllib.request
    import vulcan_service as svc
    from vulcan.metrics import METRICS
    monkeypatch.setenv("VULCAN_HEALTH_PORT", "18799")
    from vulcan.logging_setup import get_logger
    srv = svc.start_health_server(get_logger("vulcan.test"))
    assert srv is not None
    try:
        METRICS.record_cycle({"at": "now", "alerts": [], "resolved": [],
                              "work_orders_raised": [],
                              "notifications": [], "sla_breaches": []})
        with urllib.request.urlopen(
                "http://127.0.0.1:18799/healthz", timeout=5) as r:
            body = _json.loads(r.read().decode())
        assert r.status == 200 if hasattr(r, "status") else True
        assert body["cycles_total"] >= 1
        assert "uptime_s" in body and "errors_total" in body
    finally:
        srv.shutdown()


def test_service_once_mode_runs_single_cycle(tmp_path, monkeypatch):
    import vulcan_service as svc
    import sentinel as sn
    import vulcan.tools.live as live
    monkeypatch.setattr(sn, "ALERTS_DIR", tmp_path / "alerts")
    monkeypatch.setattr(live, "LOGBOOK_PATH", tmp_path / "logbook.md")
    monkeypatch.setenv("VULCAN_HEALTH_PORT", "0")     # no port in CI
    rc = svc.main(["--once"])
    assert rc == 0
    assert (tmp_path / "alerts").exists(), \
        "one service cycle must produce the seeded autonomous alerts"


def test_secrets_never_reach_log_lines(tmp_path, monkeypatch):
    import logging
    from vulcan.logging_setup import JsonFormatter
    rec = logging.LogRecord("vulcan", logging.INFO, "x", 1,
                            "key is sk-ant-abc123DEF456 ok", (), None)
    out = JsonFormatter().format(rec)
    assert "sk-ant-abc123DEF456" not in out
    assert "REDACTED" in out
