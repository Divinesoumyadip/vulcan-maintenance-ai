"""Maintenance prioritization — Section 5.2 of the problem statement, verbatim.

The spec demands prioritization based on FOUR named criteria:
    1. process criticality
    2. delay severity
    3. spares availability
    4. procurement lead time

This tool computes a transparent multi-criteria priority score per asset by
fusing the other deterministic layers (plant health scan, delay log, CMMS
spares catalog, criticality register). Every factor is reported with its raw
value, its normalized 0-100 sub-score, its weight, and its data source —
so the ranking is fully explainable and auditable (constraint C-07 friendly:
nothing is invented; unmapped data is flagged, never guessed).

Scoring model (stated heuristic, weights configurable):
    priority = 0.40 * health_risk        (100 - worst health score; live scan)
             + 0.20 * criticality        (register value * 10)
             + 0.25 * delay_severity     (asset share of total lost minutes)
             + 0.15 * spares_exposure    (stock-out risk scaled by lead time)
"""
from __future__ import annotations

import json

import pandas as pd

from vulcan.config import DATA_DIR, DELAY_LOG_PATH, SPARES_DB_PATH
from vulcan.tools.anomaly import scan_plant_health

CRITICALITY_PATH = DATA_DIR / "criticality.json"

WEIGHTS = {
    "health_risk": 0.40,
    "delay_severity": 0.25,
    "criticality": 0.20,
    "spares_exposure": 0.15,
}
DEFAULT_CRITICALITY = 5  # used ONLY with an explicit flag, never silently
LEAD_TIME_CAP_DAYS = 30.0


def _criticality_map() -> dict:
    if not CRITICALITY_PATH.exists():
        return {}
    return json.loads(CRITICALITY_PATH.read_text()).get("assets", {})


def _delay_minutes_per_asset() -> dict[str, float]:
    if not DELAY_LOG_PATH.exists():
        return {}
    df = pd.read_csv(DELAY_LOG_PATH)
    if df.empty:
        return {}
    return df.groupby("equipment_id")["delay_minutes"].sum().to_dict()


def _spares_exposure(equipment_id: str) -> tuple[float, dict]:
    """Stock-out exposure 0-100 from the CMMS catalog + provenance detail."""
    try:
        catalog = json.loads(SPARES_DB_PATH.read_text())
    except FileNotFoundError:
        return 70.0, {"flag": "CMMS_UNAVAILABLE",
                      "detail": "spares catalog not readable — exposure "
                                "assumed elevated (70), verify manually"}
    def _compatible(item: dict) -> bool:
        # compatible_equipment may be a single ID or a ';'-separated list
        # ("CC2-MO-01; UTIL-HPU-03") — match on membership, never equality.
        raw = str(item.get("compatible_equipment", ""))
        return equipment_id in {p.strip() for p in raw.split(";") if p.strip()}

    items = [i for i in catalog if isinstance(i, dict) and _compatible(i)]
    if not items:
        return 70.0, {"flag": "NO_COMPATIBLE_SPARE_LISTED",
                      "detail": "no catalog item maps to this asset — "
                                "treated as elevated exposure (70), "
                                "confirm with stores"}
    in_stock = [i for i in items if i.get("on_hand_qty", 0) > 0]
    min_lead = min(i.get("lead_time_days", LEAD_TIME_CAP_DAYS) for i in items)
    lead_norm = min(min_lead, LEAD_TIME_CAP_DAYS) / LEAD_TIME_CAP_DAYS * 100
    if in_stock:
        # Stock on hand: residual exposure is thin-stock + replenishment lead.
        thin = any(i.get("on_hand_qty", 0) <= 1 for i in in_stock)
        score = 0.25 * lead_norm + (15.0 if thin else 0.0)
        detail = {"items_listed": len(items), "items_in_stock": len(in_stock),
                  "min_lead_time_days": min_lead,
                  "thin_stock_qty_le_1": thin}
    else:
        # Stock-out: exposure is dominated by procurement lead time.
        score = 40.0 + 0.6 * lead_norm
        detail = {"items_listed": len(items), "items_in_stock": 0,
                  "min_lead_time_days": min_lead, "flag": "STOCK_OUT"}
    return round(min(score, 100.0), 1), detail


def rank_maintenance_priorities() -> dict:
    """Rank every known asset by the four Section-5.2 criteria."""
    scan = scan_plant_health()
    if scan.get("status") not in (None, "OK") and not scan.get("assets"):
        return {"status": "NO_DATA",
                "message": "no sensor-backed assets found to prioritize"}

    # Worst health score per asset across its monitored parameters.
    worst_health: dict[str, dict] = {}
    for a in scan.get("assets", []):
        eq = a["equipment_id"]
        if eq not in worst_health \
                or a["health_score"] < worst_health[eq]["health_score"]:
            worst_health[eq] = a

    delays = _delay_minutes_per_asset()
    max_delay = max(delays.values()) if delays else 0.0
    crit_map = _criticality_map()

    assets = sorted(set(worst_health) | set(delays) | set(crit_map))
    ranked, flags_global = [], []
    for eq in assets:
        factors, flags = {}, []

        h = worst_health.get(eq)
        if h:
            hr = round(100.0 - h["health_score"], 1)
            factors["health_risk"] = {
                "sub_score": hr,
                "raw": {"worst_health_score": h["health_score"],
                        "worst_parameter": h["parameter"],
                        "status": h["status"],
                        "layers_fired": h["layers_fired"]},
                "source": "scan_plant_health (Tier-1 sensor readings)"}
        else:
            factors["health_risk"] = {
                "sub_score": 0.0,
                "raw": None,
                "source": "no sensor readings stored for this asset"}
            flags.append("NO_CONDITION_DATA — health risk unknown, "
                         "scored 0 by construction, NOT measured healthy")

        dm = float(delays.get(eq, 0.0))
        ds = round(dm / max_delay * 100.0, 1) if max_delay else 0.0
        factors["delay_severity"] = {
            "sub_score": ds,
            "raw": {"lost_minutes": dm},
            "source": "data/delay_log.csv (Tier-2 historical)"}

        if eq in crit_map:
            cv = crit_map[eq]["criticality_1_10"]
            factors["criticality"] = {
                "sub_score": cv * 10.0,
                "raw": {"criticality_1_10": cv,
                        "rationale": crit_map[eq].get("rationale", "")},
                "source": "data/criticality.json (reliability register)"}
        else:
            factors["criticality"] = {
                "sub_score": DEFAULT_CRITICALITY * 10.0,
                "raw": {"criticality_1_10": DEFAULT_CRITICALITY},
                "source": "DEFAULT — asset absent from criticality register"}
            flags.append("CRITICALITY_NOT_MAPPED — default 5/10 used, "
                         "confirm with reliability team")

        se, se_detail = _spares_exposure(eq)
        factors["spares_exposure"] = {
            "sub_score": se, "raw": se_detail,
            "source": "data/spares.json (CMMS catalog, Tier-1 stock read)"}
        if se_detail.get("flag"):
            flags.append(f"SPARES: {se_detail['flag']}")

        total = round(sum(WEIGHTS[k] * factors[k]["sub_score"]
                          for k in WEIGHTS), 1)
        contributions = {k: round(WEIGHTS[k] * factors[k]["sub_score"], 1)
                         for k in WEIGHTS}
        band = ("CRITICAL" if total >= 70 else
                "HIGH" if total >= 50 else
                "MEDIUM" if total >= 30 else "LOW")
        ranked.append({"equipment_id": eq,
                       "priority_score_0_100": total,
                       "priority_band": band,
                       "weighted_contributions": contributions,
                       "factors": factors,
                       "flags": flags})
        flags_global.extend(flags)

    ranked.sort(key=lambda r: r["priority_score_0_100"], reverse=True)
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    return {
        "status": "OK",
        "method": "weighted multi-criteria fusion per problem-statement "
                  "Section 5.2: process criticality, delay severity, "
                  "spares availability, procurement lead time "
                  "(+ live condition risk)",
        "weights": WEIGHTS,
        "ranking": ranked,
        "evidence_tier": "Tier-1/Tier-2 fusion — every sub-score carries "
                         "its raw value and source; scores are a stated "
                         "heuristic, not a measurement",
        "honesty_flags": sorted(set(flags_global)),
    }
