"""Delay-log analytics (VULCAN agent A3b's real engine).

CSV format: data/delay_log.csv with columns
  date,equipment_id,section,cause,delay_minutes

Computes: Pareto ranking by lost time, time-between-failure (TBF) trend per
asset, chronic repeat offenders (>=3 recurrences of the same cause on the
same asset), and the bottleneck candidate (asset with the largest total
lost time). All values come from the log — nothing is invented.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from vulcan.config import DELAY_LOG_PATH


def analyze_delay_log(equipment_id: str | None = None) -> dict:
    if not DELAY_LOG_PATH.exists():
        return {"status": "NO_DATA",
                "message": "data/delay_log.csv not found — raise an "
                           "INFORMATION GAP."}
    df = pd.read_csv(DELAY_LOG_PATH)
    needed = {"date", "equipment_id", "cause", "delay_minutes"}
    if not needed.issubset(df.columns):
        return {"status": "BAD_FORMAT",
                "message": f"delay_log.csv must contain columns {sorted(needed)}"}
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "delay_minutes"])
    if equipment_id:
        df = df[df["equipment_id"] == equipment_id]
    if df.empty:
        return {"status": "NO_DATA",
                "message": "no delay records match the filter"}

    total_lost = float(df["delay_minutes"].sum())

    # Pareto by cause
    pareto = (df.groupby("cause")["delay_minutes"].agg(["sum", "count"])
                .sort_values("sum", ascending=False).reset_index())
    pareto["share_pct"] = (pareto["sum"] / total_lost * 100).round(1)
    pareto_rows = [
        {"cause": r["cause"], "lost_minutes": float(r["sum"]),
         "events": int(r["count"]), "share_pct": float(r["share_pct"])}
        for _, r in pareto.iterrows()
    ]

    # TBF trend per asset: compare first-half vs second-half mean gap
    tbf_trends = {}
    for asset, g in df.sort_values("date").groupby("equipment_id"):
        if len(g) < 4:
            tbf_trends[asset] = {"trend": "INSUFFICIENT_EVENTS",
                                 "events": int(len(g))}
            continue
        gaps_h = g["date"].diff().dropna().dt.total_seconds().to_numpy() / 3600
        half = len(gaps_h) // 2
        early, late = float(np.mean(gaps_h[:half])), float(np.mean(gaps_h[half:]))
        if late > early * 1.15:
            trend = "IMPROVING"      # failures getting further apart
        elif late < early * 0.85:
            trend = "DETERIORATING"  # failures getting closer together
        else:
            trend = "STABLE"
        tbf_trends[asset] = {"trend": trend, "events": int(len(g)),
                             "mean_tbf_hours_early": round(early, 1),
                             "mean_tbf_hours_recent": round(late, 1)}

    # Chronic repeat offenders: same asset + same cause >= 3 times
    rep = (df.groupby(["equipment_id", "cause"]).size()
             .reset_index(name="recurrences"))
    chronic = [
        {"equipment_id": r["equipment_id"], "cause": r["cause"],
         "recurrences": int(r["recurrences"])}
        for _, r in rep[rep["recurrences"] >= 3].iterrows()
    ]

    # Bottleneck candidate: asset gating the most time
    by_asset = (df.groupby("equipment_id")["delay_minutes"].sum()
                  .sort_values(ascending=False))
    bottleneck = {"equipment_id": str(by_asset.index[0]),
                  "lost_minutes": float(by_asset.iloc[0]),
                  "share_pct": round(float(by_asset.iloc[0]) / total_lost
                                     * 100, 1)}

    return {
        "status": "OK",
        "records": int(len(df)),
        "date_range": [str(df["date"].min().date()),
                       str(df["date"].max().date())],
        "total_lost_minutes": total_lost,
        "pareto_by_cause": pareto_rows,
        "tbf_trend_per_asset": tbf_trends,
        "chronic_repeat_offenders": chronic,
        "bottleneck_candidate": bottleneck,
        "evidence_tier": 2,
        "note": "Historical breakdown records → Tier-2 evidence (VULCAN Sec 6).",
    }
