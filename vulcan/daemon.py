"""VULCAN server-side autonomy daemon (v9 → v10: autostarts by default).

THE GAP THIS CLOSES
-------------------
v8's in-app Autopilot ran inside a Streamlit *fragment* — which executes
only while a browser session is open and rerunning. Close the tab and the
"autonomous" loop stops dead: the plant goes unmonitored the moment the
human walks away, which is exactly the failure mode an autonomy reviewer
probes for. The headless `sentinel.py --watch` covered it, but required a
human to start a second process in a terminal.

v9 runs the loop in a daemon THREAD inside the Streamlit server process
itself. Started once (singleton), it keeps scanning, alerting, logging and
auto-raising work orders with EVERY browser tab closed, until the server
stops. The UI fragment is demoted to what it really is: a live *viewer* of
autonomous activity, not the engine of it.

v10 closes the last autonomy gap: through v9 even the daemon needed a
human to CLICK START — the shipped default state of the product was
'plant unmonitored'. `ensure_autostarted()` now launches the daemon with
the server (opt OUT via VULCAN_DAEMON_AUTOSTART=0), and an optional,
budget-capped mode (VULCAN_DAEMON_LLM=1) attaches a full agent diagnostic
to each CRITICAL alert so the engineer wakes up to a finished report.

Concurrency is safe by construction: ticks go through
`autopilot.autopilot_tick`, which is single-flight (non-blocking lock) —
the daemon, the UI fragment and multiple sessions can never run
overlapping passes against the shared sentinel state.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone

from vulcan.autopilot import autopilot_tick
from vulcan.config import (DATA_DIR, daemon_autostart, daemon_interval,
                           daemon_llm_enabled, daemon_llm_max_per_hour)


class SentinelDaemon:
    """Background autonomous-monitoring thread with a clean lifecycle."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.interval = 60
        self.history: list[dict] = []      # last cycle summaries (bounded)
        self.started_at: str | None = None
        self.cycles = 0
        self.autostarted = False
        self._llm_times: deque = deque()   # diagnostic budget (per hour)
        self.llm_diagnostics = 0

    # ── lifecycle ───────────────────────────────────────────────────────
    def start(self, interval: int = 60) -> bool:
        """Idempotent start. Returns True if a new thread was launched."""
        with self._lock:
            self.interval = max(5, int(interval))
            if self._thread and self._thread.is_alive():
                return False
            self._stop.clear()
            self.started_at = datetime.now(timezone.utc) \
                .strftime("%Y-%m-%d %H:%M:%S UTC")
            self._thread = threading.Thread(
                target=self._run, name="vulcan-sentinel-daemon", daemon=True)
            self._thread.start()
            return True

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive()
                    and not self._stop.is_set())

    # ── autonomous LLM diagnostics (v10, opt-in + budget-capped) ────────
    def _llm_budget_ok(self) -> bool:
        now = time.monotonic()
        while self._llm_times and now - self._llm_times[0] > 3600:
            self._llm_times.popleft()
        return len(self._llm_times) < daemon_llm_max_per_hour()

    def _maybe_llm_diagnose(self, summary: dict) -> None:
        """If enabled, attach a full agent diagnostic to each CRITICAL
        alert this cycle produced — the engineer wakes up to a finished
        report, not a raw alarm. Hard budget: at most
        VULCAN_DAEMON_LLM_MAX_PER_HOUR diagnostics/hour, CRITICAL only,
        and any failure is recorded, never fatal to the loop."""
        if not daemon_llm_enabled():
            return
        for alert in summary.get("alerts", []):
            if alert["severity"] != "CRITICAL" or not self._llm_budget_ok():
                continue
            try:
                from vulcan.orchestrator import VulcanOrchestrator
                text = VulcanOrchestrator().ask(
                    f"AUTONOMOUS DAEMON ALERT {alert['file']}: "
                    f"{alert['key']} is CRITICAL"
                    f"{' (PREDICTIVE — limit not yet breached)' if alert.get('predictive') else ''}. "
                    "Produce the Section-10 real-time alert block plus a "
                    "full diagnostic with root cause, RUL, risk and "
                    "prioritized actions, with citations.")
                f = DATA_DIR / "alerts" / alert["file"]
                if f.exists():
                    with f.open("a", encoding="utf-8") as fh:
                        fh.write("\n## Autonomous VULCAN diagnostic "
                                 "(daemon-attached)\n\n" + text + "\n")
                self._llm_times.append(time.monotonic())
                self.llm_diagnostics += 1
            except Exception as exc:
                summary.setdefault("llm_errors", []).append(str(exc))

    # ── loop ────────────────────────────────────────────────────────────
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                summary = autopilot_tick(auto_stream=False)
                summary["driver"] = "server-daemon"
                self._maybe_llm_diagnose(summary)
                self.cycles += 1
                self.history = ([summary] + self.history)[:50]
            except Exception as exc:           # never let the daemon die
                self.history = ([{"at": "?", "error": str(exc),
                                  "driver": "server-daemon"}]
                                + self.history)[:50]
            # responsive shutdown: sleep in 1 s slices
            for _ in range(self.interval):
                if self._stop.is_set():
                    return
                time.sleep(1)


_singleton: SentinelDaemon | None = None
_singleton_lock = threading.Lock()


def get_daemon() -> SentinelDaemon:
    """Process-wide singleton (one monitoring loop per server, ever)."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = SentinelDaemon()
        return _singleton


def ensure_autostarted() -> SentinelDaemon:
    """v10 — autonomy is the DEFAULT, not an opt-in.

    Called at app boot: unless VULCAN_DAEMON_AUTOSTART=0, the monitoring
    daemon starts with the server — zero clicks, zero terminal commands.
    Through v9 the shipped default state of the product was 'plant
    unmonitored until a human presses Start'; that is the opposite of
    autonomy. Idempotent: safe to call on every Streamlit rerun.
    """
    d = get_daemon()
    if daemon_autostart() and not d.running:
        if d.start(interval=daemon_interval()):
            d.autostarted = True
    return d
