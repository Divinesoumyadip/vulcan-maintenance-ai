"""VULCAN production service (v11) — autonomy as a first-class process.

Through v10 the always-on loop lived either in a Streamlit daemon thread
(dies with the web server, shares its resources) or the bare CLI sentinel
(no health reporting, no graceful shutdown). Real deployments run
monitoring as its OWN service. This is that service:

    python vulcan_service.py --interval 60

  * **Fail-fast startup** — `validate_config()` runs first; a
    misconfigured autonomous system refuses to start (exit 2) instead of
    running wrong quietly. Errors are printed plainly.
  * **Graceful shutdown** — SIGTERM/SIGINT finish the in-flight cycle and
    exit 0; state is transactional (SQLite WAL), so a hard kill is also
    safe — the next start resumes from committed de-dup state.
  * **Health endpoint** — GET /healthz on VULCAN_HEALTH_PORT (default
    8799, 0 disables) returns JSON metrics: uptime, cycles, alerts,
    work orders, SLA breaches, storms, errors, last-cycle timestamp.
    Wire it to any uptime checker / k8s liveness probe / Grafana.
  * **Structured logs** — JSON lines to stdout + rotating file, secrets
    redacted (vulcan/logging_setup.py).
  * **Same brain** — every cycle is the identical, test-pinned
    `autopilot_tick` the UI daemon and headless sentinel use. There is
    exactly one autonomy semantics in this system.

systemd / Docker recipes: see PRODUCTION.md.
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from vulcan.autopilot import autopilot_tick
from vulcan.config import daemon_interval, health_port, validate_config
from vulcan.logging_setup import get_logger, log_event
from vulcan.metrics import METRICS

_stop = threading.Event()


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):                                    # noqa: N802
        if self.path not in ("/healthz", "/health", "/"):
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps(METRICS.snapshot(), indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):                           # quiet: own logs
        pass


def start_health_server(log: logging.Logger) -> ThreadingHTTPServer | None:
    port = health_port()
    if port <= 0:
        return None
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    except OSError as exc:
        log_event(log, logging.WARNING, "health endpoint unavailable",
                  port=port, error=str(exc))
        return None
    threading.Thread(target=srv.serve_forever,
                     name="vulcan-healthz", daemon=True).start()
    log_event(log, logging.INFO, "health endpoint up", port=port,
              path="/healthz")
    return srv


def _handle_signal(signum, _frame):
    _stop.set()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="VULCAN autonomy service")
    parser.add_argument("--interval", type=int, default=daemon_interval(),
                        help="seconds between autonomous passes")
    parser.add_argument("--once", action="store_true",
                        help="run a single cycle and exit (cron mode)")
    args = parser.parse_args(argv)

    log = get_logger("vulcan.service")

    errors = validate_config()
    if errors:
        for e in errors:
            print(f"CONFIG ERROR: {e}", file=sys.stderr)
        log_event(log, logging.ERROR, "refusing to start: invalid config",
                  errors=errors)
        return 2

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    health = start_health_server(log)
    interval = max(5, args.interval)
    log_event(log, logging.INFO, "VULCAN service started",
              interval_s=interval, once=args.once)

    try:
        while not _stop.is_set():
            try:
                summary = autopilot_tick(auto_stream=False)
                summary["driver"] = "service"
            except Exception as exc:        # the loop must never die
                METRICS.record_error(exc)
                log.exception("cycle failed")
            if args.once:
                break
            for _ in range(interval):       # responsive shutdown
                if _stop.is_set():
                    break
                time.sleep(1)
    finally:
        if health is not None:
            health.shutdown()
        log_event(log, logging.INFO, "VULCAN service stopped gracefully",
                  **METRICS.snapshot())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
