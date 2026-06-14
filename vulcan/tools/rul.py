"""RUL estimation — linear-regression drift model with an honest 80% CI.

Implements the 'gradual drift → linear regression' branch of VULCAN Section 7.
If <5 points: returns INDICATIVE_ONLY (3-4 points) or INCALCULABLE (<3),
matching the system prompt's data-discipline rules. The CI is built from the
standard error of the regression slope (t-distribution, 80% two-sided).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats  # available via scikit-learn's scipy dependency

from vulcan.tools.anomaly import _load_series, _load_thresholds


def estimate_rul(equipment_id: str, parameter: str,
                 critical_threshold: float | None = None) -> dict:
    """Tool entrypoint. Extrapolates the recent trend to the critical limit."""
    series = _load_series(equipment_id, parameter)
    if series is None or len(series) < 3:
        return {"status": "INCALCULABLE",
                "reason": "fewer than 3 timestamped readings available",
                "minimum_data_plan": "collect >=5 timestamped readings of "
                                     f"'{parameter}' on {equipment_id} at a "
                                     "fixed sampling interval"}

    # Resolve threshold: explicit arg > thresholds.csv critical value.
    unit = str(series["unit"].iloc[-1]) if "unit" in series.columns else "unknown"
    if critical_threshold is None:
        th = _load_thresholds()
        if th is not None:
            row = th[(th["equipment_id"] == equipment_id)
                     & (th["parameter"] == parameter)]
            if len(row):
                critical_threshold = float(row["critical"].iloc[0])
    if critical_threshold is None:
        return {"status": "INCALCULABLE",
                "reason": "no critical threshold supplied and none found in "
                          "thresholds.csv — VULCAN must not invent one (C-07)",
                "minimum_data_plan": "provide the OEM critical limit for "
                                     f"'{parameter}' or add it to "
                                     "data/sensor_data/thresholds.csv"}

    t0 = series["timestamp"].iloc[0]
    hours = (series["timestamp"] - t0).dt.total_seconds().to_numpy() / 3600.0
    y = series["value"].astype(float).to_numpy()
    n = len(y)

    reg = stats.linregress(hours, y)
    slope, intercept = float(reg.slope), float(reg.intercept)
    latest_t, latest_v = float(hours[-1]), float(y[-1])

    status = "OK" if n >= 5 else "INDICATIVE_ONLY"

    rising_to_threshold = (critical_threshold > latest_v and slope > 0) or \
                          (critical_threshold < latest_v and slope < 0)
    if not rising_to_threshold or abs(slope) < 1e-12:
        return {"status": "NOT_DEGRADING_TOWARD_THRESHOLD",
                "equipment_id": equipment_id, "parameter": parameter,
                "unit": unit, "latest_value": latest_v,
                "critical_threshold": critical_threshold,
                "slope_per_hour": round(slope, 6), "n_readings": n,
                "note": "Trend is not moving toward the critical limit; "
                        "linear-drift RUL does not apply. Consider another "
                        "Section-7 model or continued monitoring."}

    # Point estimate: hours from the LATEST reading until threshold crossing.
    hours_to_cross = (critical_threshold - (intercept + slope * latest_t)) / slope
    point = max(0.0, hours_to_cross)

    # 80% CI from slope uncertainty (delta method on crossing time).
    if reg.stderr and reg.stderr > 0 and n > 2:
        t_crit = stats.t.ppf(0.90, df=n - 2)  # two-sided 80%
        slope_lo = slope - t_crit * reg.stderr
        slope_hi = slope + t_crit * reg.stderr

        def cross(s: float) -> float:
            if s == 0 or (s > 0) != (slope > 0):
                return float("inf")  # CI bound includes "never crosses"
            return max(0.0, (critical_threshold
                             - (intercept + s * latest_t)) / s)

        bounds = sorted([cross(slope_lo), cross(slope_hi)])
        ci_low, ci_high = bounds[0], bounds[1]
    else:
        ci_low, ci_high = point, float("inf")

    def fmt(h: float) -> str:
        return "unbounded (slope CI includes zero)" if np.isinf(h) \
            else f"{h:.1f} h"

    return {
        "status": status,
        "model": "linear_regression_drift (VULCAN Section 7)",
        "equipment_id": equipment_id,
        "parameter": parameter,
        "unit": unit,
        "n_readings": n,
        "inputs_used": ["timestamped readings", "critical threshold"],
        "latest_value": latest_v,
        "critical_threshold": critical_threshold,
        "degradation_rate_per_hour": round(slope, 6),
        "r_squared": round(float(reg.rvalue) ** 2, 3),
        "rul_point_estimate_hours": round(point, 1),
        "rul_ci80_hours": [fmt(ci_low), fmt(ci_high)],
        "caveat": ("Only 3-4 points — trend is [INDICATIVE ONLY] per "
                   "VULCAN Section 7." if status == "INDICATIVE_ONLY"
                   else "Linear extrapolation assumes the current degradation "
                        "mechanism continues unchanged."),
    }


def estimate_rul_weibull(beta: float, eta_hours: float,
                         age_hours: float) -> dict:
    """Weibull conditional-RUL (Section 7: rotational wear branch).

    Given shape beta, scale eta (characteristic life, hours) and current age,
    returns the conditional median residual life and an 80% band from the
    conditional survival function R(t|age) = exp(((age/eta)^beta
    - ((age+t)/eta)^beta)). All inputs must be SUPPLIED (OEM/Tier data) —
    this tool never assumes them (C-07).
    """
    if beta <= 0 or eta_hours <= 0 or age_hours < 0:
        return {"status": "INVALID_INPUT",
                "message": "beta>0, eta_hours>0, age_hours>=0 required"}

    a = (age_hours / eta_hours) ** beta

    def t_at_reliability(r: float) -> float:
        # solve R(t|age)=r  ->  t = eta*(a - ln r)^(1/beta) - age
        return eta_hours * (a - np.log(r)) ** (1.0 / beta) - age_hours

    median = t_at_reliability(0.5)
    lo, hi = t_at_reliability(0.9), t_at_reliability(0.1)  # 80% band
    return {
        "status": "OK",
        "model": "weibull_conditional (VULCAN Section 7)",
        "inputs_used": {"beta": beta, "eta_hours": eta_hours,
                        "age_hours": age_hours},
        "rul_median_hours": round(median, 1),
        "rul_band80_hours": [round(lo, 1), round(hi, 1)],
        "hazard_now_per_hour": round(
            beta / eta_hours * (age_hours / eta_hours) ** (beta - 1), 8)
        if age_hours > 0 else 0.0,
        "caveat": "Validity depends on the supplied beta/eta genuinely "
                  "describing this equipment class and duty; cite their "
                  "source in the Evidence Register.",
    }


BOLTZMANN_EV = 8.617e-5  # eV/K


def estimate_rul_arrhenius(ea_ev: float, design_temp_c: float,
                           actual_temp_c: float, design_life_hours: float,
                           hours_consumed: float) -> dict:
    """Arrhenius thermal-aging RUL (Section 7: thermal aging branch).

    Life at actual temperature: L_actual = L_design *
    exp(Ea/k * (1/T_actual - 1/T_design)) with temperatures in kelvin.
    All inputs (activation energy Ea, design temp, rated life) must be
    SUPPLIED from OEM/standard data — never assumed (C-07).
    """
    if ea_ev <= 0 or design_life_hours <= 0 or hours_consumed < 0:
        return {"status": "INVALID_INPUT",
                "message": "ea_ev>0, design_life_hours>0, "
                           "hours_consumed>=0 required"}
    t_design_k = design_temp_c + 273.15
    t_actual_k = actual_temp_c + 273.15
    if t_design_k <= 0 or t_actual_k <= 0:
        return {"status": "INVALID_INPUT",
                "message": "temperatures below absolute zero"}
    accel = float(np.exp(ea_ev / BOLTZMANN_EV
                         * (1.0 / t_design_k - 1.0 / t_actual_k)))
    life_actual = design_life_hours / accel
    remaining = max(0.0, life_actual - hours_consumed)
    return {
        "status": "OK",
        "model": "arrhenius_thermal_aging (VULCAN Section 7)",
        "inputs_used": {"ea_ev": ea_ev, "design_temp_c": design_temp_c,
                        "actual_temp_c": actual_temp_c,
                        "design_life_hours": design_life_hours,
                        "hours_consumed": hours_consumed},
        "acceleration_factor_vs_design": round(accel, 3),
        "expected_life_at_actual_temp_hours": round(life_actual, 1),
        "rul_point_estimate_hours": round(remaining, 1),
        "caveat": "Assumes constant operating temperature and a single "
                  "thermally-activated degradation mechanism. Ea source "
                  "must be cited in the Evidence Register; result has no "
                  "statistical CI — treat as a planning estimate and pair "
                  "with condition monitoring.",
    }


def estimate_rul_pf_interval(pf_interval_hours: float,
                             hours_since_p_detected: float) -> dict:
    """P-F interval RUL (Section 7: alarm-already-tripped branch).

    Given the OEM/standard P-F interval (time from detectable potential
    failure 'P' to functional failure 'F') and the elapsed time since the
    P-condition was detected, returns the remaining window. Both inputs
    must be SUPPLIED — never assumed (C-07).
    """
    if pf_interval_hours <= 0 or hours_since_p_detected < 0:
        return {"status": "INVALID_INPUT",
                "message": "pf_interval_hours>0, "
                           "hours_since_p_detected>=0 required"}
    remaining = pf_interval_hours - hours_since_p_detected
    return {
        "status": "OK" if remaining > 0 else "WINDOW_EXPIRED",
        "model": "pf_interval (VULCAN Section 7)",
        "inputs_used": {"pf_interval_hours": pf_interval_hours,
                        "hours_since_p_detected": hours_since_p_detected},
        "rul_window_remaining_hours": round(max(0.0, remaining), 1),
        "fraction_of_window_consumed": round(
            min(1.0, hours_since_p_detected / pf_interval_hours), 2),
        "caveat": "P-F intervals are equipment-class estimates with wide "
                  "real-world variance; treat the remaining window as an "
                  "upper bound for scheduling, not a guarantee. If the "
                  "window is expired, the asset may fail at any time — "
                  "escalate immediately.",
    }
