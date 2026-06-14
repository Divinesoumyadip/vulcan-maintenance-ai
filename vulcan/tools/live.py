"""UI-side utilities: simulated live IoT feed + persistent digital logbook.

These are host utilities (used by the dashboard/monitor tabs), deliberately
NOT exposed as LLM tools — the agent must never be able to create sensor
data, only read it (honesty by construction).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from vulcan.config import DATA_DIR, SENSOR_DATA_DIR

LOGBOOK_PATH = DATA_DIR / "logbook.md"
READINGS = SENSOR_DATA_DIR / "readings.csv"
# Synthetic streamed readings live in their OWN overlay file so the pristine
# demo dataset (and the 33/33 eval baseline) can always be restored with one
# click — see reset_live_stream().
LIVE_STREAM = SENSOR_DATA_DIR / "live_stream.csv"


def _all_readings() -> pd.DataFrame:
    frames = [pd.read_csv(READINGS)]
    if LIVE_STREAM.exists():
        frames.append(pd.read_csv(LIVE_STREAM))
    return pd.concat(frames, ignore_index=True)


def reset_live_stream() -> bool:
    """Delete the synthetic overlay, restoring the pristine demo baseline."""
    if LIVE_STREAM.exists():
        LIVE_STREAM.unlink()
        return True
    return False


def simulate_next_reading(equipment_id: str, parameter: str,
                          hours_ahead: float = 8.0,
                          trend_source: str = "all") -> dict:
    """Append one plausible next reading by continuing the recent trend.

    Clearly SYNTHETIC: tagged in the return value; used by the Live Monitor
    tab and the Autopilot to demo real-time alerting without a historian.

    trend_source:
      "all"      — slope fitted on the last 8 readings INCLUDING earlier
                   synthetic ones (v7 behavior; suitable for one-off manual
                   streaming).
      "baseline" — slope fitted on the last 8 readings of the ORIGINAL
                   dataset only. Used by the Autopilot demo: prevents the
                   feedback flatline where the simulator fits a slope to its
                   own noisy output and the seeded degradation stalls below
                   the trip limit.
    """
    df = _all_readings()
    sel = df[(df["equipment_id"] == equipment_id)
             & (df["parameter"] == parameter)].copy()
    if len(sel) < 3:
        return {"status": "NO_DATA"}
    sel["timestamp"] = pd.to_datetime(sel["timestamp"])
    sel = sel.sort_values("timestamp")

    if trend_source == "baseline":
        base = pd.read_csv(READINGS)
        base = base[(base["equipment_id"] == equipment_id)
                    & (base["parameter"] == parameter)].copy()
        base["timestamp"] = pd.to_datetime(base["timestamp"])
        fit = base.sort_values("timestamp").tail(8)
    else:
        fit = sel.tail(8)

    hrs = (fit["timestamp"] - fit["timestamp"].iloc[0]) \
        .dt.total_seconds().to_numpy() / 3600.0
    vals = fit["value"].astype(float).to_numpy()
    slope = float(np.polyfit(hrs, vals, 1)[0]) if hrs[-1] > 0 else 0.0
    noise_scale = 0.25 if trend_source == "baseline" else 0.5
    noise = float(np.std(vals)) * noise_scale or 0.05
    new_val = float(sel["value"].astype(float).iloc[-1]
                    + slope * hours_ahead + np.random.normal(0, noise))
    new_ts = sel["timestamp"].iloc[-1] + timedelta(hours=hours_ahead)
    unit = str(sel["unit"].iloc[-1]) if "unit" in sel.columns else ""
    new_header = not LIVE_STREAM.exists()
    pd.DataFrame([[new_ts, equipment_id, parameter, round(new_val, 2), unit]],
                 columns=["timestamp", "equipment_id", "parameter",
                          "value", "unit"]).to_csv(
        LIVE_STREAM, mode="a", header=new_header, index=False)
    return {"status": "OK", "synthetic": True, "timestamp": str(new_ts),
            "equipment_id": equipment_id, "parameter": parameter,
            "value": round(new_val, 2), "unit": unit}


# ───────────────────────── digital logbook ─────────────────────────

def append_logbook(entry_text: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    block = f"\n\n---\n### Entry — {stamp}\n\n{entry_text.strip()}\n"
    if not LOGBOOK_PATH.exists():
        LOGBOOK_PATH.write_text("# VULCAN Digital Maintenance Logbook\n",
                                encoding="utf-8")
    with LOGBOOK_PATH.open("a", encoding="utf-8") as f:
        f.write(block)


def read_logbook() -> str:
    if LOGBOOK_PATH.exists():
        return LOGBOOK_PATH.read_text(encoding="utf-8")
    return "# VULCAN Digital Maintenance Logbook\n\n*(empty — save a " \
           "report from the Chat tab to start it)*"
