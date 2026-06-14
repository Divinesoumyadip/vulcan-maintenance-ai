"""Layered anomaly detection over sensor CSVs (VULCAN agent A3's real engine).

Layers (mirroring system-prompt Section 3 / A3):
  L1 threshold breach vs configured limit
  L2 statistical deviation (z-score vs baseline window)
  L3 CUSUM drift detection
  L4 trend direction & rate (slope of recent window)

CSV format: timestamp,equipment_id,parameter,value,unit
An optional thresholds row convention: thresholds live in data/sensor_data/thresholds.csv
with columns equipment_id,parameter,warning,critical,unit.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from vulcan import datastore

BASELINE_FRACTION = 0.4   # first 40% of the series is treated as baseline
Z_LIMIT = 3.0
CUSUM_K_SIGMA = 1.0       # slack in sigmas (robust for short series)
CUSUM_H_SIGMA = 8.0       # decision interval in sigmas


def _load_thresholds() -> pd.DataFrame | None:
    """v8: served from the mtime-cached datastore (no per-call disk read)."""
    return datastore.get_thresholds()


def _load_series(equipment_id: str, parameter: str) -> pd.DataFrame | None:
    """v8: sliced from the in-memory cache instead of re-reading every CSV.

    Cache invalidates automatically when any file in data/sensor_data/
    changes (mtime fingerprint), so freshness semantics are identical to v7
    — just ~50x fewer disk reads per fleet scan.
    """
    return datastore.get_series(equipment_id, parameter)


def detect_anomalies(equipment_id: str, parameter: str) -> dict:
    """Tool entrypoint. Returns layered findings + raw stats, never opinions."""
    series = _load_series(equipment_id, parameter)
    if series is None or len(series) < 3:
        return {
            "status": "NO_DATA",
            "equipment_id": equipment_id,
            "parameter": parameter,
            "message": "No (or <3) readings found in data/sensor_data/ for "
                       "this equipment/parameter. Raise an INFORMATION GAP.",
        }

    values = series["value"].astype(float).to_numpy()
    unit = str(series["unit"].iloc[-1]) if "unit" in series.columns else "unknown"
    timestamps = series["timestamp"]
    n = len(values)
    split = max(3, int(n * BASELINE_FRACTION))
    baseline, recent = values[:split], values[split:]
    mu = float(np.mean(baseline))
    # sigma floor (1% of |mean|) prevents near-zero-noise series from
    # making the CUSUM decision interval trivially small (false positives)
    sigma = max(float(np.std(baseline, ddof=1)), abs(mu) * 0.01, 1e-9)
    latest = float(values[-1])

    findings: list[dict] = []

    # L1 — threshold breach
    th = _load_thresholds()
    threshold_info = None
    if th is not None:
        row = th[(th["equipment_id"] == equipment_id)
                 & (th["parameter"] == parameter)]
        if len(row):
            warn = float(row["warning"].iloc[0])
            crit = float(row["critical"].iloc[0])
            threshold_info = {"warning": warn, "critical": crit,
                              "unit": str(row["unit"].iloc[0])}
            if latest >= crit:
                findings.append({"layer": "L1_THRESHOLD", "severity": "CRITICAL",
                                 "detail": f"latest {latest} {unit} >= critical "
                                           f"limit {crit} {unit}"})
            elif latest >= warn:
                findings.append({"layer": "L1_THRESHOLD", "severity": "WARNING",
                                 "detail": f"latest {latest} {unit} >= warning "
                                           f"limit {warn} {unit}"})

    # L2 — z-score vs baseline
    z = (latest - mu) / sigma
    if abs(z) >= Z_LIMIT:
        findings.append({"layer": "L2_ZSCORE", "severity": "WARNING",
                         "detail": f"latest deviates {z:.1f} sigma from "
                                   f"baseline mean {mu:.2f} {unit}"})

    # L3 — CUSUM drift
    k, h = CUSUM_K_SIGMA * sigma, CUSUM_H_SIGMA * sigma
    s_pos = s_neg = 0.0
    cusum_fired_at = None
    for i, v in enumerate(values):
        s_pos = max(0.0, s_pos + (v - mu) - k)
        s_neg = max(0.0, s_neg - (v - mu) - k)
        if (s_pos > h or s_neg > h) and cusum_fired_at is None:
            cusum_fired_at = str(timestamps.iloc[i])
    if cusum_fired_at:
        findings.append({"layer": "L3_CUSUM", "severity": "WARNING",
                         "detail": f"sustained drift from baseline detected, "
                                   f"first signalled at {cusum_fired_at}"})

    # L4 — trend rate on the recent window
    trend = None
    if len(recent) >= 3:
        hours = (timestamps.iloc[split:] - timestamps.iloc[split]) \
            .dt.total_seconds().to_numpy() / 3600.0
        if hours[-1] > 0:
            slope = float(np.polyfit(hours, recent, 1)[0])
            trend = {"slope_per_hour": round(slope, 4), "unit": unit,
                     "direction": "rising" if slope > 0
                     else ("falling" if slope < 0 else "flat")}
            findings.append({"layer": "L4_TREND", "severity": "INFO",
                             "detail": f"recent trend {trend['direction']} at "
                                       f"{slope:.4f} {unit}/h"})

    # Data-quality flags (feeds A1)
    quality_flags = []
    if float(np.std(values[-min(10, n):])) == 0.0:
        quality_flags.append("FROZEN_VALUE: last readings are flatlined — "
                             "possible dead sensor")
    span_h = (timestamps.iloc[-1] - timestamps.iloc[0]).total_seconds() / 3600.0

    return {
        "status": "OK",
        "equipment_id": equipment_id,
        "parameter": parameter,
        "unit": unit,
        "n_readings": n,
        "time_span_hours": round(span_h, 1),
        "first_timestamp": str(timestamps.iloc[0]),
        "last_timestamp": str(timestamps.iloc[-1]),
        "baseline_mean": round(mu, 3),
        "baseline_sigma": round(sigma, 3),
        "latest_value": latest,
        "z_score_latest": round(z, 2),
        "thresholds": threshold_info,
        "trend": trend,
        "layers_fired": findings,
        "data_quality_flags": quality_flags,
        "evidence_tier": 1,
        "note": "Timestamped sensor readings → Tier-1 evidence (VULCAN Sec 6).",
    }


def scan_plant_health(include_detections: bool = False) -> dict:
    """Fleet-wide health scan: every (equipment, parameter) pair on file.

    Health score 0-100 (100 = pristine), derived transparently:
      - position between warning/critical limits (when thresholds exist)
      - |z| vs baseline otherwise
      - penalty for an adverse trend toward the limit
    The formula is heuristic and stated as such — a ranking aid, not a verdict.

    include_detections (v9): when True, the full per-pair detect_anomalies
    payloads are returned under "detections" (keyed "EQ::PARAM"). The
    sentinel/autopilot use this so a pass runs the layered engine ONCE per
    pair instead of twice (v8 recomputed every detection a second time).
    Kept off for the LLM tool path to avoid doubling the tool-result tokens.
    """
    pairs = datastore.list_pairs()  # v8: single cached read, no re-glob
    assets = []
    detections: dict[str, dict] = {}
    for eq, param in sorted(pairs):
        r = detect_anomalies(eq, param)
        if r.get("status") != "OK":
            continue
        if include_detections:
            detections[f"{eq}::{param}"] = r
        latest, unit = r["latest_value"], r["unit"]
        th = r.get("thresholds")
        if th:
            warn, crit = th["warning"], th["critical"]
            if latest >= crit:
                score = 0.0
            elif latest >= warn:  # 40 -> 5 between warning and critical
                score = 40.0 - 35.0 * (latest - warn) / max(crit - warn, 1e-9)
            else:                  # 100 -> 40 from baseline mean to warning
                base = r["baseline_mean"]
                frac = max(0.0, min(1.0, (latest - base)
                                    / max(warn - base, 1e-9)))
                score = 100.0 - 60.0 * frac
        else:
            score = max(0.0, 100.0 - 12.0 * abs(r["z_score_latest"]))
        trend = r.get("trend") or {}
        if trend.get("direction") == "rising" and th and latest < th["critical"]:
            score = max(0.0, score - 5.0)
        sev = ("CRITICAL" if score < 15 else
               "WARNING" if score < 45 else
               "WATCH" if score < 70 else "HEALTHY")
        assets.append({
            "equipment_id": eq, "parameter": param,
            "latest_value": latest, "unit": unit,
            "health_score": round(score, 1), "status": sev,
            "layers_fired": [f["layer"] for f in r["layers_fired"]],
            "trend": trend.get("direction"),
            "last_timestamp": r["last_timestamp"],
        })
    assets.sort(key=lambda a: a["health_score"])
    out = {"status": "OK", "n_assets_scanned": len(assets), "assets": assets,
           "scoring_note": "Heuristic ranking aid (position vs limits + "
                           "trend penalty); not a substitute for diagnosis.",
           "evidence_tier": 1}
    if include_detections:
        out["detections"] = detections
    return out
