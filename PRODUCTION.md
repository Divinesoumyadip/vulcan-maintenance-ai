# PRODUCTION.md — operating VULCAN beyond the demo

This guide is for the team that has to keep VULCAN alive at 03:00, not
the one demoing it at 15:00. It documents the v11 production layer, the
exact operational contracts, and — honestly — where the prototype's
boundaries are and what replaces each piece at plant scale.

## 1. Run modes (one brain, three bodies)

Every mode executes the identical, test-pinned `autopilot_tick`; there is
exactly one autonomy semantics in this system.

| Mode | Command | Use |
|---|---|---|
| **Service** (recommended) | `python vulcan_service.py --interval 60` | Production autonomy: own process, SIGTERM-graceful, `/healthz`, structured logs |
| Cron one-shot | `python vulcan_service.py --once` | Air-gapped/scheduled environments |
| UI-embedded daemon | `streamlit run app.py` (autostarts) | Demos, single-engineer workstations |
| Headless sentinel | `python sentinel.py --watch 300` | Legacy twin; still supported |

When the service owns autonomy, set `VULCAN_DAEMON_AUTOSTART=0` on the UI
so there is exactly one writer of autonomous events (the compose file
does this for you).

## 2. Configuration contract

All knobs are environment variables, read **at call time** (no restart
needed for most), validated **fail-fast at service start** — a
misconfigured autonomous system refuses to run rather than running wrong.

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Chat agent + optional daemon diagnostics |
| `VULCAN_DB_PATH` | `data/vulcan.db` | SQLite WAL store (orders, notifications, state) |
| `VULCAN_DAEMON_AUTOSTART` | `1` | UI autostarts the in-process daemon |
| `VULCAN_DAEMON_INTERVAL` | `60` | Seconds between passes (min 5, validated) |
| `VULCAN_RUL_WARN_HOURS` / `VULCAN_RUL_CRIT_HOURS` | `72` / `24` | Predictive horizons (crit must be < warn, validated) |
| `VULCAN_WO_SLA_MIN` | `60` | OPEN CRITICAL order older ⇒ SLA escalation |
| `VULCAN_MAX_ALERTS_PER_CYCLE` | `25` | Alert-storm guard cap |
| `VULCAN_RETENTION_DAYS` | `30` | Prune alert files + notification rows (0 = off; work orders never pruned) |
| `VULCAN_HEALTH_PORT` | `8799` | `/healthz` JSON metrics (0 = off) |
| `VULCAN_WEBHOOK_URL` | — | Notification fan-out sink |
| `VULCAN_WEBHOOK_SECRET` | — | HMAC-SHA256 `X-Vulcan-Signature` on every POST |
| `VULCAN_DAEMON_LLM` / `_MAX_PER_HOUR` | `0` / `4` | Budget-capped autonomous diagnostics |
| `VULCAN_LOG_LEVEL` / `VULCAN_LOG_DIR` | `INFO` / `data/logs` | Structured JSON logging |

## 3. Storage, backup, recovery

State lives in **one SQLite database in WAL mode** (`data/vulcan.db`):
work orders (the audit trail — never auto-pruned), role-routed
notifications, and sentinel de-dup state, all transactional. Legacy v10
JSON files are migrated automatically on first open and renamed
`*.migrated`.

* **Backup**: copy `vulcan.db` + `vulcan.db-wal` + `vulcan.db-shm`
  together, or `sqlite3 data/vulcan.db ".backup backup.db"` online.
* **Crash recovery**: none needed — WAL replays committed transactions;
  uncommitted work simply never happened. A hard `kill -9` mid-cycle
  resumes from the last committed de-dup state; at worst one cycle's
  alerts re-evaluate (and de-dup absorbs repeats).
* **Concurrency**: many readers + one writer with 30 s busy-timeout;
  in-process writes additionally serialized. Single host only — see the
  scaling path for multi-host.

## 4. Security posture

* Container runs as **non-root** (`uid 10001`), writes only `/app/data`.
* **Secrets only via environment**; the JSON log formatter actively
  redacts anything matching an Anthropic key pattern (test-pinned:
  `test_secrets_never_reach_log_lines`).
* Webhooks are **HMAC-SHA256 signed** when `VULCAN_WEBHOOK_SECRET` is
  set — receivers must verify `X-Vulcan-Signature` so a forged "all
  clear" or fake CRITICAL can't be injected into downstream channels.
* The agent's authority boundary is unchanged from day one: it reads
  data and writes reports/orders/notifications; it never actuates
  equipment. A human authorizes every physical action.
* `/healthz` exposes counters only — no plant data, no secrets.

## 5. Monitoring & SLOs

`GET :8799/healthz` returns JSON: uptime, `cycles_total`,
`alerts_total`, `predictive_alerts_total`, `work_orders_total`,
`sla_breaches_total`, `alert_storms_total`, `errors_total`,
`last_cycle_at`, `last_error`.

Suggested alerts on the monitor-of-the-monitor:
* `now - last_cycle_at > 3 × interval` → autonomy stalled (page).
* `errors_total` increasing across scrapes → degraded (ticket).
* `alert_storms_total` increment → systemic plant/data event (page).

Logs are JSON lines (stdout + rotating `data/logs/vulcan.log`, 5 MB × 3)
— ship them to ELK/Loki/Splunk with any standard collector.

## 6. systemd unit (bare-metal)

```ini
[Unit]
Description=VULCAN autonomy service
After=network.target

[Service]
User=vulcan
WorkingDirectory=/opt/vulcan
Environment=VULCAN_HEALTH_PORT=8799
ExecStart=/usr/bin/python3 vulcan_service.py --interval 60
Restart=on-failure
TimeoutStopSec=90

[Install]
WantedBy=multi-user.target
```

SIGTERM finishes the in-flight cycle and exits 0 (test-pinned graceful
stop path); `Restart=on-failure` plus WAL state makes recovery
hands-free.

## 7. Failure-mode behavior (designed, not accidental)

| Event | Behavior |
|---|---|
| Alert storm (> cap new alerts in one pass) | ONE roll-up report + ONE CRITICAL notification; per-event pipeline suppressed that cycle; de-dup state still updated |
| Webhook endpoint down | Recorded as `failed:<Error>` on the notification record; loop never blocks or dies |
| LLM API down | Autonomy unaffected (deterministic layers); daemon diagnostics fail-soft and are budget-capped anyway |
| Bad configuration | Service exits 2 at startup with plain-language errors |
| Disk filling | Retention prunes alert files + notification rows each cycle; logs rotate; work orders are the kept audit trail |
| Process killed mid-cycle | WAL: committed state intact; next start resumes |

## 8. Scaling path — what this prototype honestly is, and what replaces each piece

The demo runs 6 assets; a steel plant has tens of thousands of tags.
The architecture was shaped so each component has a named, drop-in
successor rather than a rewrite:

| Prototype component | Plant-scale replacement | Why the swap is clean |
|---|---|---|
| SQLite WAL store | PostgreSQL (+ TimescaleDB for readings) | Schema is portable SQL; `vulcan/db.py` is the single seam |
| CSV sensor files + mtime cache | Plant historian / OPC-UA / MQTT ingest | All reads go through `vulcan/datastore.py` |
| Per-pair Python scan loop | Partitioned workers (shard by asset class) per service instance | `sentinel_pass` is pure per-pair logic |
| TF-IDF/BM25 retrieval | pgvector / managed vector store + re-ranker | Retrieval is one tool behind one interface |
| Webhook fan-out | Kafka / plant message bus | `notify()` is the single emit point |
| In-process metrics + `/healthz` | Prometheus client + Grafana | `Metrics.snapshot()` maps 1:1 to counters |
| Linear-drift autonomous RUL | Per-failure-mode models (Weibull/Arrhenius already implemented; survival models next) | `_predictive_severity` is the single policy gate |

## 9. Known limits (read before trusting)

* Single-host writer model; multi-site needs the PostgreSQL step.
* The seeded data is SYNTHETIC and labeled as such everywhere; predictive
  CIs on it are unrealistically tight because the noise is small — on
  real noise the CI widens and the INDICATIVE_ONLY cap does its job.
* No SSO/RBAC on the UI (the role filter is a view, not access control);
  front it with your reverse-proxy auth until that lands.
* The LLM dependency is external (Anthropic API); autonomy is designed
  to be fully functional without it.
