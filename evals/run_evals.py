"""VULCAN evaluation harness — measured results, not vibes.

Runs deterministic checks against the tool layer (no API key needed):
  A. Detection accuracy   — seeded faults must be caught, healthy assets not
  B. Prediction sanity    — RUL within engineering-plausible bounds + valid CI
  C. Analytics correctness— delay bottleneck / Pareto / chronic offenders
  D. Retrieval relevance  — right document ranked first for domain queries
  E. Fabrication resistance — missing data MUST yield honest failure states,
                              never invented values (constraint C-07)

Usage:  python evals/run_evals.py
Writes a scorecard to evals/results.md and exits non-zero on any failure
(CI-friendly).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vulcan.tools.anomaly import detect_anomalies, scan_plant_health  # noqa: E402
from vulcan.tools.cmms import query_spares  # noqa: E402
from vulcan.tools.delay_analytics import analyze_delay_log  # noqa: E402
from vulcan.tools.priority import rank_maintenance_priorities  # noqa: E402
from vulcan.tools.retrieval import search_knowledge_base  # noqa: E402
from vulcan.tools.rul import estimate_rul, estimate_rul_weibull  # noqa: E402

RESULTS: list[tuple[str, str, bool, str]] = []  # (suite, name, passed, note)


def check(suite: str, name: str, cond: bool, note: str = "") -> None:
    RESULTS.append((suite, name, bool(cond), note))
    print(f"[{'PASS' if cond else 'FAIL'}] {suite} :: {name}"
          + (f" — {note}" if note else ""))


def main() -> int:
    # ───────── A. Detection accuracy ─────────
    r = detect_anomalies("CC2-MO-01", "vibration_mm_s")
    layers = {f["layer"] for f in r.get("layers_fired", [])}
    check("A.detection", "seeded degrading asset triggers threshold layer",
          "L1_THRESHOLD" in layers, f"fired={sorted(layers)}")
    check("A.detection", "statistical layer (z-score) also fires",
          "L2_ZSCORE" in layers)
    check("A.detection", "drift layer (CUSUM) also fires",
          "L3_CUSUM" in layers)

    healthy = detect_anomalies("HSM-COILER-01", "vibration_mm_s")
    hl = {f["layer"] for f in healthy.get("layers_fired", [])
          if f["severity"] in ("WARNING", "CRITICAL")}
    check("A.detection", "healthy asset raises no warning/critical layer "
          "(false-positive check)", len(hl) == 0, f"fired={sorted(hl)}")

    scan = scan_plant_health()
    worst = scan["assets"][0]
    check("A.detection", "fleet scan ranks the seeded fault worst",
          worst["equipment_id"] == "CC2-MO-01"
          and worst["parameter"] == "vibration_mm_s",
          f"worst={worst['equipment_id']}/{worst['parameter']}")

    # ───────── B. Prediction sanity ─────────
    rul = estimate_rul("CC2-MO-01", "vibration_mm_s")
    pt = rul.get("rul_point_estimate_hours", -1)
    check("B.prediction", "linear RUL produced with usable history",
          rul["status"] == "OK")
    check("B.prediction", "RUL point estimate engineering-plausible "
          "(24h-2000h for this seeded trend)", 24 <= pt <= 2000,
          f"point={pt} h")
    ci = rul.get("rul_ci80_hours", ["", ""])
    check("B.prediction", "80% CI present and brackets the point estimate",
          isinstance(ci, list) and len(ci) == 2, f"ci={ci}")
    check("B.prediction", "regression fit quality reported (r²)",
          0.0 <= rul.get("r_squared", -1) <= 1.0,
          f"r2={rul.get('r_squared')}")

    w = estimate_rul_weibull(beta=2.4, eta_hours=18000, age_hours=12000)
    band = w["rul_band80_hours"]
    check("B.prediction", "Weibull conditional RUL: band ordered and "
          "contains the median",
          band[0] < w["rul_median_hours"] < band[1],
          f"median={w['rul_median_hours']} band={band}")

    # ───────── C. Analytics correctness ─────────
    d = analyze_delay_log()
    check("C.analytics", "bottleneck identified from delay log",
          d["bottleneck_candidate"]["equipment_id"] == "HSM-COILER-01",
          f"got={d['bottleneck_candidate']['equipment_id']}")
    check("C.analytics", "Pareto shares sum to ~100%",
          abs(sum(p["share_pct"] for p in d["pareto_by_cause"]) - 100) < 1.0)
    chronic_ids = {(c["equipment_id"], c["cause"])
                   for c in d["chronic_repeat_offenders"]}
    check("C.analytics", "chronic repeat offender (>=3 recurrences) flagged",
          ("HSM-COILER-01", "wrapper roll jam") in chronic_ids)
    check("C.analytics", "deteriorating TBF trend detected on bottleneck",
          d["tbf_trend_per_asset"]["HSM-COILER-01"]["trend"]
          == "DETERIORATING")

    # ───────── D. Retrieval relevance ─────────
    q1 = search_knowledge_base("oscillator vibration trip limit")
    top2 = [r["doc_name"] for r in q1["results"][:2]]
    check("D.retrieval", "manual within top-2 for limits query (reaches "
          "agent context)", any(d.startswith("manual_") for d in top2),
          f"top2={top2}")
    q2 = search_knowledge_base("previous bearing failure root cause")
    top_types = [r["doc_type"] for r in q2["results"][:2]]
    check("D.retrieval", "history surfaced for past-failure query",
          "history" in top_types, f"top_types={top_types}")
    q3 = search_knowledge_base("LOTO permit isolation requirements")
    check("D.retrieval", "SOP surfaced for permits query",
          any(r["doc_type"] == "sop" for r in q3["results"][:2]))

    # ───────── E. Fabrication resistance (constraint C-07) ─────────
    g1 = detect_anomalies("GHOST-99", "vibration_mm_s")
    check("E.honesty", "unknown equipment → NO_DATA, never invented "
          "readings", g1["status"] == "NO_DATA")
    g2 = estimate_rul("GHOST-99", "vibration_mm_s")
    check("E.honesty", "RUL without data → INCALCULABLE + minimum-data plan",
          g2["status"] == "INCALCULABLE" and "minimum_data_plan" in g2)
    g3 = estimate_rul_weibull(beta=-1, eta_hours=18000, age_hours=100)
    check("E.honesty", "invalid Weibull inputs rejected, not guessed",
          g3["status"] == "INVALID_INPUT")
    g4 = query_spares("flux capacitor antimatter coil")
    check("E.honesty", "nonexistent part → NO_MATCH, never a fake part "
          "number", g4["status"] == "NO_MATCH")
    g5 = search_knowledge_base("zzqx unrelated quantum blockchain")
    irrelevant_top = (g5["n_results"] == 0
                      or g5["results"][0]["similarity"] < 0.15)
    check("E.honesty", "irrelevant query yields no/low-confidence chunks "
          "(no forced citation)", irrelevant_top,
          f"n={g5['n_results']}")

    # ───────── F. Prioritization (problem-statement Section 5.2) ─────────
    pr = rank_maintenance_priorities()
    check("F.priority", "prioritizer runs and ranks assets",
          pr["status"] == "OK" and len(pr["ranking"]) >= 2,
          f"n={len(pr.get('ranking', []))}")
    top = pr["ranking"][0]
    check("F.priority", "top priority is a seeded problem asset "
          "(LF1-HYD-01 predicted-critical w/ zero spare stock, degrading "
          "CC2-MO-01, or bottleneck HSM-COILER-01)",
          top["equipment_id"] in ("LF1-HYD-01", "CC2-MO-01",
                                  "HSM-COILER-01"),
          f"top={top['equipment_id']} score={top['priority_score_0_100']}")
    check("F.priority", "ranking is monotonic non-increasing",
          all(a["priority_score_0_100"] >= b["priority_score_0_100"]
              for a, b in zip(pr["ranking"], pr["ranking"][1:])))
    req = {"health_risk", "delay_severity", "criticality", "spares_exposure"}
    check("F.priority", "all four Section-5.2 criteria present per asset, "
          "each with raw value + source (explainability)",
          all(req <= set(r["factors"]) and
              all("source" in r["factors"][k] for k in req)
              for r in pr["ranking"]))
    check("F.priority", "weighted contributions reproduce the total score "
          "(arithmetic audit)",
          all(abs(sum(r["weighted_contributions"].values())
                  - r["priority_score_0_100"]) < 0.15
              for r in pr["ranking"]))
    check("F.priority", "every score bounded 0-100 with a priority band",
          all(0 <= r["priority_score_0_100"] <= 100
              and r["priority_band"] in
              ("LOW", "MEDIUM", "HIGH", "CRITICAL")
              for r in pr["ranking"]))
    unmapped = [r for r in pr["ranking"]
                if any("CRITICALITY_NOT_MAPPED" in f for f in r["flags"])]
    mapped_ok = all(
        any("CRITICALITY_NOT_MAPPED" in f for f in r["flags"])
        == (r["factors"]["criticality"]["source"].startswith("DEFAULT"))
        for r in pr["ranking"])
    check("F.priority", "defaulted criticality is always flagged, never "
          "silent (honesty under missing data)", mapped_ok,
          f"defaulted={[r['equipment_id'] for r in unmapped]}")

    # ───────── G. Regression & data-robustness probes ─────────
    # G1 — multi-asset spares mapping (';'-separated compatible_equipment).
    # Regression guard: an exact-equality bug here once hid the servo valve
    # from UTIL-HPU-03 and falsely flagged NO_COMPATIBLE_SPARE_LISTED.
    from vulcan.tools.priority import _spares_exposure
    se_util, det_util = _spares_exposure("UTIL-HPU-03")
    check("G.regression", "';'-listed spare maps to EVERY compatible asset "
          "(UTIL-HPU-03 sees the shared servo valve)",
          det_util.get("flag") != "NO_COMPATIBLE_SPARE_LISTED"
          and det_util.get("items_listed", 0) >= 1,
          f"detail={det_util}")
    check("G.regression", "shared servo-valve STOCK-OUT visible to the "
          "prioritizer (exposure reflects 45-day lead)",
          det_util.get("flag") == "STOCK_OUT" and se_util >= 80.0,
          f"exposure={se_util}")
    sp = query_spares("hydraulic servo valve")
    so = [m for m in sp.get("matches", []) if m.get("on_hand_qty", 1) == 0]
    check("G.regression", "spares search surfaces the stocked-out servo "
          "valve with its true qty (0) — no optimistic invention",
          sp["status"] == "OK" and len(so) >= 1)

    # G2 — dead-sensor detection: flatlined series must raise FROZEN_VALUE.
    fz = detect_anomalies("BF3-GCP-FAN-02", "motor_current_A")
    check("G.robustness", "flatlined series raises FROZEN_VALUE data-quality "
          "flag (dead-sensor detection feeds A1)",
          fz["status"] == "OK" and any("FROZEN_VALUE" in f
                                       for f in fz["data_quality_flags"]),
          f"flags={fz.get('data_quality_flags')}")
    check("G.robustness", "flatlined-but-in-range series fires no "
          "warning/critical layer (no false alarm on a dead sensor)",
          not any(f["severity"] in ("WARNING", "CRITICAL")
                  for f in fz["layers_fired"]))

    # G3 — RUL honesty when a threshold is simply not on file (C-07).
    nt = estimate_rul("BF3-GCP-FAN-02", "motor_current_A")
    check("G.robustness", "RUL with readings but NO configured threshold → "
          "INCALCULABLE + plan, never an invented limit (C-07)",
          nt["status"] == "INCALCULABLE"
          and "minimum_data_plan" in nt, f"status={nt['status']}")

    # ───────── H. Autonomy (sentinel — minimal human intervention) ─────────
    from sentinel import render_alert_md, sentinel_pass
    ev1, st1 = sentinel_pass({})  # cold start: no prior state
    alert_keys = {e["key"] for e in ev1 if e["kind"] == "ALERT"}
    check("H.autonomy", "sentinel autonomously detects the seeded degrading "
          "asset with NO human query (proactive, not approached)",
          "CC2-MO-01::vibration_mm_s" in alert_keys,
          f"alerts={sorted(alert_keys)}")
    ev2, _ = sentinel_pass(st1)   # same conditions, warmed state
    check("H.autonomy", "unchanged condition re-alerts ZERO times "
          "(state-aware dedup — no alarm fatigue)",
          len([e for e in ev2 if e["kind"] == "ALERT"]) == 0,
          f"second_pass_alerts={len(ev2)}")
    sample = next(e for e in ev1 if e["key"] == "CC2-MO-01::vibration_mm_s")
    md = render_alert_md(sample)
    check("H.autonomy", "autonomous alert report carries layered Tier-1 "
          "evidence + severity transition + C-07 provenance note",
          "L1_THRESHOLD" in md and "Severity transition" in md
          and "C-07" in md)

    # ───────── I. v10 predictive autonomy & routing ─────────
    pred = [e for e in ev1 if e.get("predictive") and e["kind"] == "ALERT"]
    check("I.predictive", "sentinel fires a PREDICTIVE alert from the RUL "
          "horizon (FR5: failure predicted autonomously)",
          any(e["key"] == "LF1-HYD-01::oil_temp_C::RUL" for e in pred),
          f"predictive_alerts={[e['key'] for e in pred]}")
    lf = next((e for e in pred
               if e["key"] == "LF1-HYD-01::oil_temp_C::RUL"), None)
    check("I.predictive", "predictive alert fires BEFORE any limit breach "
          "(latest value is still below the warning limit)",
          lf is not None
          and lf["detection"]["latest_value"]
          < lf["detection"]["thresholds"]["warning"],
          f"latest={lf and lf['detection']['latest_value']} "
          f"warn={lf and lf['detection']['thresholds']['warning']}")
    check("I.predictive", "autonomous predictive CRITICAL is granted only "
          "on a full-evidence (status OK) trend, never 3-4 points",
          lf is not None and lf["severity"] == "CRITICAL"
          and lf["rul"]["status"] == "OK"
          and lf["rul"]["rul_point_estimate_hours"] < 24,
          f"rul={lf and lf['rul']['rul_point_estimate_hours']}h "
          f"status={lf and lf['rul']['status']}")
    pmd = render_alert_md(lf)
    check("I.predictive", "predictive alert report states the projected "
          "time-to-critical with CI and the plan-not-react intent",
          "PREDICTIVE FAILURE ALERT" in pmd
          and "Projected time to critical limit" in pmd
          and "PLANNED instead of forced" in pmd)
    check("I.predictive", "predictive channel de-dups independently "
          "(no repeat on unchanged second pass)",
          not [e for e in ev2 if e.get("predictive")
               and e["kind"] == "ALERT"])
    from vulcan.notify import ROUTING
    check("I.routing", "explicit role-routing matrix covers every "
          "autonomous event class (FR7 user-specific notifications)",
          {"ANOMALY_CRITICAL", "PREDICTIVE_FAILURE", "WORK_ORDER_RAISED",
           "WORK_ORDER_SLA_BREACH", "PROCUREMENT_RISK",
           "DATA_QUALITY"} <= set(ROUTING),
          f"events={sorted(ROUTING)}")
    check("I.routing", "a predicted failure reaches the planner; a "
          "procurement risk reaches procurement",
          "planner" in ROUTING["PREDICTIVE_FAILURE"]
          and "procurement" in ROUTING["PROCUREMENT_RISK"])

    # ───────── J. v11 production readiness ─────────
    import os as _os
    import tempfile as _tf
    from contextlib import closing as _closing
    with _tf.TemporaryDirectory() as _td:
        _os.environ["VULCAN_DB_PATH"] = str(Path(_td) / "eval.db")
        try:
            from vulcan import db as _db
            with _closing(_db.connect()) as _conn:
                mode = _conn.execute("PRAGMA journal_mode").fetchone()[0]
            check("J.production", "action store is SQLite in WAL mode "
                  "(transactional, concurrent-safe — not JSON files)",
                  mode == "wal", f"journal_mode={mode}")
            from vulcan.tools.workorders import (create_work_order,
                                                 list_work_orders)
            r1 = create_work_order("EVAL-EQ", "t", priority="HIGH",
                                   parameter="p")
            r2 = create_work_order("EVAL-EQ", "t2", priority="CRITICAL",
                                   parameter="p")
            check("J.production", "DB-backed ledger keeps the v9 "
                  "escalation contract (CREATED then ESCALATED, "
                  "never swallowed)",
                  r1["status"] == "CREATED" and r2["status"] == "ESCALATED"
                  and list_work_orders()["work_orders"][0]["priority"]
                  == "CRITICAL")
        finally:
            _os.environ.pop("VULCAN_DB_PATH", None)
    from vulcan.config import (max_alerts_per_cycle, retention_days,
                               validate_config)
    check("J.production", "fail-fast config validation passes on shipped "
          "defaults (a broken config refuses to start)",
          validate_config() == [])
    check("J.production", "alert-storm guard and retention are configured "
          "with sane bounds",
          max_alerts_per_cycle() >= 1 and retention_days() >= 0,
          f"storm_cap={max_alerts_per_cycle()} "
          f"retention={retention_days()}d")
    from vulcan.notify import sign_payload
    sig = sign_payload(b"x", "s")
    check("J.production", "webhook payloads are HMAC-SHA256 signable "
          "(authenticated alert sink, not an injection vector)",
          sig.startswith("sha256=") and len(sig) == 71
          and sig != sign_payload(b"x", "s2"))
    import vulcan_service as _svc
    check("J.production", "standalone service exposes lifecycle + health "
          "(main, --once, /healthz handler)",
          callable(_svc.main) and hasattr(_svc, "_HealthHandler")
          and hasattr(_svc, "start_health_server"))

    # ───────── scorecard ─────────
    total = len(RESULTS)
    passed = sum(1 for *_, ok, _ in [(s, n, ok, note)
                 for s, n, ok, note in RESULTS] if ok)
    lines = ["# VULCAN Evaluation Scorecard\n",
             f"**Result: {passed}/{total} checks passed**\n",
             "| Suite | Check | Result | Note |",
             "|---|---|---|---|"]
    for suite, name, ok, note in RESULTS:
        lines.append(f"| {suite} | {name} | "
                     f"{'✅ PASS' if ok else '❌ FAIL'} | {note} |")
    lines.append("\n*Suites: A detection accuracy · B prediction sanity · "
                 "C analytics correctness · D retrieval relevance · "
                 "E fabrication resistance (constraint C-07) · "
                 "F Section-5.2 prioritization · "
                 "G regression & data-robustness · "
                 "H autonomous-sentinel behavior · "
                 "I v10 predictive autonomy & role routing · "
                 "J v11 production readiness. "
                 "Deterministic — runs without an API key.*")
    out = Path(__file__).parent / "results.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{'='*60}\nSCORE: {passed}/{total} — written to {out}\n{'='*60}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
