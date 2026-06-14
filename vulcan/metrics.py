"""Operational metrics (v11) — counters the /healthz endpoint exposes.

An unattended system that cannot answer "are you alive, when did you last
work, how much have you done, how often do you fail" is unsupervisable.
This is a deliberately dependency-free in-process registry (no Prometheus
client needed for the prototype); PRODUCTION.md maps the upgrade path.
Thread-safe; snapshot() is what /healthz serializes.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone


class Metrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self.started_at = datetime.now(timezone.utc).isoformat(
            timespec="seconds")
        self.counters: dict[str, int] = {
            "cycles_total": 0, "alerts_total": 0,
            "predictive_alerts_total": 0, "work_orders_total": 0,
            "notifications_total": 0, "sla_breaches_total": 0,
            "errors_total": 0, "alert_storms_total": 0,
            "pruned_alerts_total": 0, "pruned_notifications_total": 0,
        }
        self.last_cycle_at: str | None = None
        self.last_error: str | None = None

    def inc(self, name: str, n: int = 1) -> None:
        with self._lock:
            self.counters[name] = self.counters.get(name, 0) + n

    def record_cycle(self, summary: dict) -> None:
        with self._lock:
            self.counters["cycles_total"] += 1
            alerts = summary.get("alerts", [])
            self.counters["alerts_total"] += len(alerts)
            self.counters["predictive_alerts_total"] += sum(
                1 for a in alerts if a.get("predictive"))
            self.counters["work_orders_total"] += len(
                summary.get("work_orders_raised", []))
            self.counters["notifications_total"] += len(
                summary.get("notifications", []))
            self.counters["sla_breaches_total"] += len(
                summary.get("sla_breaches", []))
            if summary.get("alert_storm"):
                self.counters["alert_storms_total"] += 1
            self.last_cycle_at = summary.get("at")

    def record_error(self, exc: BaseException) -> None:
        with self._lock:
            self.counters["errors_total"] += 1
            self.last_error = f"{type(exc).__name__}: {exc}"

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "status": "ok" if self.counters["errors_total"] == 0
                or self.last_cycle_at else "degraded",
                "started_at": self.started_at,
                "uptime_s": round(time.monotonic() - self._t0, 1),
                "last_cycle_at": self.last_cycle_at,
                "last_error": self.last_error,
                **self.counters,
            }


METRICS = Metrics()
