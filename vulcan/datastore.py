"""Central cached datastore for sensor readings and thresholds.

WHY THIS EXISTS (v8 efficiency fix)
-----------------------------------
v7 had every tool re-read every CSV in data/sensor_data/ from disk on every
call. `scan_plant_health` called `detect_anomalies` once per (equipment,
parameter) pair, and each of those calls re-globbed and re-parsed *all* CSVs
— O(pairs x files) disk I/O per scan. The sentinel repeated that on every
pass, and the Streamlit dashboard repeated it on every rerun.

This module loads the sensor universe ONCE into memory and invalidates the
cache only when the underlying files actually change (mtime + file-set
fingerprint). Tools now slice an in-memory DataFrame instead of hitting disk.

Honesty is preserved by construction: this layer only ever READS files —
it has no write path, so the agent still cannot create sensor data.
"""
from __future__ import annotations

import threading
from pathlib import Path

import pandas as pd

from vulcan.config import SENSOR_DATA_DIR

_lock = threading.Lock()
_cache: dict = {"fingerprint": None, "readings": None, "thresholds": None}


def _fingerprint() -> tuple:
    """Cheap change-detector: sorted (name, mtime_ns, size) of every CSV."""
    items = []
    for p in sorted(SENSOR_DATA_DIR.glob("*.csv")):
        try:
            st = p.stat()
            items.append((p.name, st.st_mtime_ns, st.st_size))
        except FileNotFoundError:   # raced with a delete — treat as changed
            items.append((p.name, -1, -1))
    return tuple(items)


def _load_from_disk() -> tuple[pd.DataFrame, pd.DataFrame | None]:
    frames = []
    thresholds = None
    needed = {"timestamp", "equipment_id", "parameter", "value"}
    for csv in sorted(SENSOR_DATA_DIR.glob("*.csv")):
        if csv.name == "thresholds.csv":
            try:
                thresholds = pd.read_csv(csv)
            except Exception:
                thresholds = None
            continue
        try:
            df = pd.read_csv(csv)
        except Exception:
            continue                       # malformed file: skip, never guess
        if not needed.issubset(df.columns):
            continue
        frames.append(df)
    if frames:
        readings = pd.concat(frames, ignore_index=True)
        readings["timestamp"] = pd.to_datetime(readings["timestamp"],
                                               errors="coerce")
        readings = readings.dropna(subset=["timestamp", "value"])
        readings = readings.sort_values("timestamp", kind="stable")
    else:
        readings = pd.DataFrame(
            columns=["timestamp", "equipment_id", "parameter", "value",
                     "unit"])
    return readings, thresholds


def _refresh_if_stale() -> None:
    fp = _fingerprint()
    if fp != _cache["fingerprint"]:
        readings, thresholds = _load_from_disk()
        _cache["readings"] = readings
        _cache["thresholds"] = thresholds
        _cache["fingerprint"] = fp


def get_all_readings() -> pd.DataFrame:
    """Combined, parsed, time-sorted readings — cached until files change."""
    with _lock:
        _refresh_if_stale()
        return _cache["readings"]


def get_thresholds() -> pd.DataFrame | None:
    with _lock:
        _refresh_if_stale()
        return _cache["thresholds"]


def get_series(equipment_id: str, parameter: str) -> pd.DataFrame | None:
    """One (equipment, parameter) series, or None if absent — from cache."""
    df = get_all_readings()
    sel = df[(df["equipment_id"] == equipment_id)
             & (df["parameter"] == parameter)]
    return sel.copy() if len(sel) else None


def list_pairs() -> list[tuple[str, str]]:
    """Every monitored (equipment, parameter) pair — from cache."""
    df = get_all_readings()
    if df.empty:
        return []
    return sorted(map(tuple, df[["equipment_id", "parameter"]]
                      .drop_duplicates().to_numpy()))


def invalidate() -> None:
    """Force a reload on next access (e.g. after a UI upload)."""
    with _lock:
        _cache["fingerprint"] = None


def fingerprint() -> tuple:
    """Public change-token for the sensor universe (v9): callers (e.g. the
    Streamlit dashboard cache) can key derived computations on this so a
    fleet scan is recomputed only when the underlying data actually
    changed, not on every UI rerun."""
    return _fingerprint()
