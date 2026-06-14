# 🔥 VULCAN — Agentic Maintenance Intelligence Core

**Tata Steel AI Hackathon 2026 · Round 2 — Agentic AI Challenge**
Problem: *Maintenance Wizard for Industrial Equipment (Steel Manufacturing)*

VULCAN is a decision-support agent for maintenance engineers. An LLM
orchestrator (governed by an 812-line behavioral system prompt, v7.0) plans
which of its **genuine tools** to call per query — RAG retrieval over
manuals/SOPs/history, a layered anomaly-detection engine, FOUR RUL estimators
(linear-drift with 80% CI, Weibull conditional, Arrhenius thermal-aging,
P-F interval), a
fleet-wide health scanner, delay-log Pareto analytics, a CMMS spares lookup,
and a persistent feedback-learning store, and an autonomous sentinel that monitors the plant unattended — then synthesizes an
evidence-traceable, confidence-calibrated answer.

Its defining property is **epistemic honesty**: VULCAN never invents a part
number, threshold, stock quantity, or sensor value. Missing data is named as
an Information Gap with an acquisition plan; every claim carries an evidence
tier and confidence band.

## What's new in v11 — Production hardening (for a plant, not a demo)

v11 takes "production-ready" literally. Ten gaps a plant operations team
would veto were closed, each pinned by a regression test (suite now **43
tests + 51 deterministic eval checks**, incl. new eval suite J):

1. **SQLite WAL store** (`vulcan/db.py`) replaces every flat-file ledger:
   work orders, role-routed notifications, and sentinel de-dup state are
   now transactional, indexed, concurrent-safe (many readers + serialized
   writers, 30 s busy-timeout), one file to back up — with **lossless
   auto-migration** of v10 JSON files on first run.
2. **Standalone autonomy service** — `python vulcan_service.py
   --interval 60`: its own process (not a web-server thread), SIGTERM
   finishes the in-flight cycle and exits 0, `--once` for cron. Same
   test-pinned `autopilot_tick` everywhere: one autonomy semantics.
3. **GET /healthz** (port 8799) — JSON metrics: uptime, cycles, alerts,
   predictive alerts, work orders, SLA breaches, storms, errors,
   last-cycle timestamp. Point a k8s liveness probe or Grafana at it.
4. **Structured JSON logging** with rotation — and a formatter that
   actively **redacts API-key patterns** from every line.
5. **Alert-storm guard**: > `VULCAN_MAX_ALERTS_PER_CYCLE` (25) new alerts
   in one pass → ONE roll-up report + ONE CRITICAL notification instead
   of a flood of files and tickets; a storm is a systemic event.
6. **Retention**: alert files + notification rows older than
   `VULCAN_RETENTION_DAYS` (30) pruned every cycle; **work orders are
   never pruned** — they're the audit trail.
7. **HMAC-signed webhooks** (`VULCAN_WEBHOOK_SECRET` →
   `X-Vulcan-Signature`) — a forged POST can't inject a fake CRITICAL or
   fake all-clear downstream.
8. **Fail-fast config validation** — inverted RUL horizons, bad
   intervals, missing data: the service refuses to start (exit 2) with
   plain-language errors instead of running wrong quietly.
9. **Hardened deployment** — non-root container, healthchecks, and a
   `docker-compose.yml` that runs autonomy as its own service with the
   UI demoted to a stateless viewer.
10. **PRODUCTION.md** — the full ops guide: run modes, backup/recovery,
    failure-mode table, SLO suggestions, systemd unit, and an honest
    **scaling path** (SQLite→PostgreSQL/TimescaleDB, CSV→historian/OPC-UA,
    metrics→Prometheus, webhook→Kafka) where every swap sits behind a
    single code seam.

## What's new in v10 — Zero-touch, PREDICTIVE autonomy (the "still not automated" verdict, killed)

v10 is a hostile audit of v9's autonomy story. The finding: even v9's
"automation" was **opt-in and reactive**. A human had to click *Start
daemon* (the shipped default state of the product was "plant
unmonitored"), and the sentinel only alerted *after* a limit or
statistical layer had already fired — it never *predicted* anything on
its own, even though four RUL models sat one tool-call away. Six gaps
were closed, each pinned by a regression test:

1. **Autonomy is now the DEFAULT, not a button.** The monitoring daemon
   autostarts with the server (`ensure_autostarted()`, opt out via
   `VULCAN_DAEMON_AUTOSTART=0`). Zero clicks, zero terminal commands —
   `streamlit run app.py` *is* the start of unattended monitoring.
   *Test: `test_daemon_autostart_honors_env`.*
2. **The sentinel is now PREDICTIVE, unattended (FR5).** Every pass runs
   the linear-drift RUL estimator on every pair, on a second de-dup
   channel (`<pair>::RUL`). Projected time-to-critical under 72 h →
   autonomous WARNING; under 24 h → autonomous CRITICAL — **before any
   limit is breached**, so intervention is *planned*, not forced.
   Stringency rule: a 3–4-point trend (INDICATIVE_ONLY) is capped at
   WARNING — an autonomous CRITICAL never rests on thin evidence.
   *Tests: `test_predictive_alert_fires_before_any_limit_breach`,
   `test_predictive_critical_requires_full_evidence`.* The seeded
   LF1-HYD-01 asset demonstrates this live: latest reading 79.3 °C
   (warning is 80), projected critical in ~20 h → predictive CRITICAL,
   auto work order, procurement flagged — all in the first cycle.
3. **Role-routed notifications (FR7, previously missing entirely).**
   Every autonomous decision is routed to the roles who must act on it
   via an explicit matrix (`vulcan/notify.py`): CRITICAL → engineer +
   supervisor; predicted failure → engineer + planner; zero-stock spare
   on a critical asset → procurement. Persisted to an append-only JSONL
   ledger; set `VULCAN_WEBHOOK_URL` to fan out to Slack/Teams/SMS.
   *Tests: `test_autopilot_routes_role_notifications`,
   `test_notification_router_persists_and_filters_by_role`.*
4. **Auto work orders are spares-aware (Section 5.2, closed loop).**
   Every autopilot-raised order now carries a live CMMS read — item,
   stock qty, lead time — and a zero-stock compatible spare fires a
   PROCUREMENT_RISK notification: the procurement constraint reaches
   procurement, autonomously.
   *Test: `test_predictive_critical_auto_raises_spares_checked_wo`.*
5. **SLA watchdog on CRITICAL orders.** An auto-raised CRITICAL order
   still OPEN past `VULCAN_WO_SLA_MIN` (default 60 min) triggers a
   one-time escalation notification — alerts can no longer die in an
   unwatched ledger. *Test: `test_wo_sla_breach_notifies_exactly_once`.*
6. **Daemon can finish the job (opt-in, budget-capped).** With
   `VULCAN_DAEMON_LLM=1` the daemon attaches a full agent diagnostic
   (root cause, RUL, risk, cited actions) to each CRITICAL alert —
   capped at `VULCAN_DAEMON_LLM_MAX_PER_HOUR` (default 4) so unattended
   autonomy can never burn an unbounded token budget.

Plus hardening: the v9 **double-driver bug** is fixed (with the daemon
running, the UI fragment is always a viewer, never a second driver);
**stale sentinel-state keys are pruned** each pass (a decommissioned
asset's severity no longer haunts the de-dup map); alert files are now
written **atomically** (tmp+rename, same policy as the state file and
ledger); and predictive reports get their own `PREDICT_*` filenames and
a 🔮 inbox badge.

(v10 suite at the time: 34 tests + 45 eval checks.)

## What's new in v9 — Stringency audit (every claim re-verified against the code)

v9 is a hostile self-review of v8: each "automated" and "efficient" claim
was checked against what the code actually does. Six defects were found
and fixed, each pinned by a regression test:

1. **Streaming was cosmetic → now genuine.** v8's `ask_stream` made a
   *blocking* `messages.create` call and re-sliced the finished text into
   80-char chunks. v9 uses the real streaming API
   (`client.messages.stream`): time-to-first-token is network latency, not
   full-generation time, and the engineer watches the agent think → call
   tools → conclude. Retries are duplication-safe (never retried after the
   first surfaced token). *Test: `test_streaming_is_genuine_not_rechunked`.*
2. **Autonomy died with the browser tab → server-side daemon.** v8's
   in-app Autopilot was a Streamlit *fragment* — it runs only while a tab
   is open. Close the tab, the plant goes unmonitored. v9 adds
   `vulcan/daemon.py`: a background thread in the server process keeps
   scanning/alerting/raising work orders with every tab closed (sidebar
   ▶ Start daemon). The fragment is demoted to a live *viewer*.
   *Test: `test_daemon_lifecycle`.*
3. **Sentinel computed every anomaly twice → once.** Each pass ran the
   layered engine inside `scan_plant_health`, then again per asset. v9's
   scan returns its detections for reuse — pass compute halved.
   *Test: `test_sentinel_runs_anomaly_engine_once_per_pair`.*
4. **Dead sensors were detected and ignored → now alerted.** A flatlined
   series raised a FROZEN_VALUE data-quality flag that autonomy then
   discarded. v9 escalates it to a WARNING alert: an unmonitored asset is
   itself an abnormal condition.
   *Test: `test_dead_sensor_escalates_to_autonomous_warning`.*
5. **Escalations were swallowed by work-order de-dup → priority bump.**
   If an open MEDIUM order existed when the autopilot saw CRITICAL, v8's
   de-dup silently kept MEDIUM. v9 raises the open order's priority and
   records the escalation note. *Test:
   `test_work_order_priority_escalates_not_swallowed`.*
6. **Learning could over-fit a single anecdote → minimum-evidence gate.**
   One CONFIRMED verdict shifted a confidence band; v9 requires n ≥ 2
   before a prior may move. *Test:
   `test_learning_prior_requires_minimum_evidence`.*

Plus robustness hardening: atomic tmp+rename writes for sentinel state and
the work-order ledger (a crash can no longer corrupt de-dup state and
cause an alarm storm); a single-flight lock so the daemon, the UI fragment
and multiple sessions can never run overlapping passes; sentinel paths
resolved at call time (fixes a test-isolation leak that wrote state into
the live data directory); the dashboard's fleet scan and delay Pareto are
now cached on the data fingerprint instead of recomputing on every
Streamlit rerun; and the Alerts inbox reads each report once, not twice.

(v9 suite at the time: 25 tests + 38 eval checks.)

## What's new in v8 — Autonomy & Efficiency

v8 directly addresses two review findings on v7 ("not automated enough"
and "inefficient"):

**Automation — the autonomous loop now runs inside the app.**

- **🤖 Autopilot**: a sidebar toggle starts a hands-free cycle (5–60 s
  period) that scans every monitored asset, fires state-aware de-duplicated
  alerts, appends the digital logbook, and **auto-raises a tracked work
  order on every CRITICAL condition** — zero human queries, zero terminal
  commands. It reuses the *exact same tested `sentinel_pass()` decision
  logic* as the headless `sentinel.py`, so in-app and unattended autonomy
  can never diverge.
- **🚨 Alerts tab**: autonomous alert reports are now first-class UI —
  an inbox with severity badges and one-click handoff to the agent.
- **🛠 Work-order ledger**: alert → action is closed automatically.
  Orders are de-duplicated while open, carry their evidence reference,
  and the agent can raise/list/update them as genuine tools (15 tools
  total) — a chat diagnosis can end in a tracked task, not just prose.
- **Closed learning loop**: engineer feedback is no longer "learning on
  request". Aggregated verdict priors are **auto-injected into the system
  context every turn** (`vulcan/learning.py`) — confirmed diagnoses raise
  confidence, refuted ones lower it, with zero extra tool calls.

**Efficiency — measured, not claimed.**

- **Cached datastore** (`vulcan/datastore.py`): all sensor CSVs load once
  into memory with mtime-fingerprint invalidation. A fleet scan now does
  **0 disk reads when warm** (v7: ≥ pairs × files reads *per scan*, on
  every dashboard rerun and every sentinel pass). Warm fleet-scan latency:
  ~12 ms. Enforced by regression test
  `test_fleet_scan_uses_single_cached_load`.
- **Parallel tool execution**: multiple tool calls in one agent round run
  concurrently (thread pool) instead of serially.
- **Context compaction**: bulky tool results older than 2 turns are
  replaced with one-line stubs, so long conversations stop paying tokens
  for stale RAG chunks (assistant prose is never touched). Combined with
  the existing prompt-cache breakpoint, per-turn cost stays flat.
- **Streaming UI**: chat answers render as they generate (made *genuinely*
  token-streamed in v9 — see the stringency audit above).
- **Resilience**: transient API errors retry with exponential backoff; an
  invalid key now produces an actionable message (and a sidebar
  **🔑 Validate key** button checks it in one click) instead of a raw
  401 dump in the chat.

## Quick start

```bash
# 1. Install (Python 3.10+)
pip install -r requirements.txt

# 2. Configure — get a key at https://console.anthropic.com
export ANTHROPIC_API_KEY=sk-ant-...        # or paste it in the app sidebar

# 3. Run it — this single command IS the start of autonomy (v10):
streamlit run app.py    # monitoring daemon autostarts with the server
#    ...or the terminal chat version:
python cli.py
#    ...or headless (cron/systemd twin of the same decision logic):
python sentinel.py --watch 300        # scan every 5 min, alert on changes
python sentinel.py --once --with-llm  # one pass + full agent diagnostics

# 4. PRODUCTION mode (v11): autonomy as its own service — fail-fast
#    config validation, graceful SIGTERM, GET :8799/healthz metrics
python vulcan_service.py --interval 60
#    ...or the full production shape (sentinel service + UI viewer):
docker compose up
```

Operations guide (backup, SLOs, failure modes, scaling path): see
**PRODUCTION.md**.

v10 knobs (all optional, defaults shown): `VULCAN_DAEMON_AUTOSTART=1`,
`VULCAN_DAEMON_INTERVAL=60`, `VULCAN_RUL_WARN_HOURS=72`,
`VULCAN_RUL_CRIT_HOURS=24`, `VULCAN_WO_SLA_MIN=60`, `VULCAN_DAEMON_LLM=0`
(+ `VULCAN_DAEMON_LLM_MAX_PER_HOUR=4`), `VULCAN_WEBHOOK_URL=` — see
`.env.example`.

The app has six tabs plus the always-on autonomy status strip:

- **🛰 Autonomy strip** (top) — shows the AUTOSTARTED server daemon ticking:
  scan → reactive + PREDICTIVE alerts → role-routed notifications →
  spares-checked auto work orders → SLA watchdog, with a per-cycle
  activity log. No clicks required; an in-tab Autopilot toggle remains as
  a viewer/fallback, and a demo mode can auto-advance the simulated IoT
  feed so judges watch a degradation get caught live.
- **💬 Chat** — the agent, with streamed answers, a tool-trace transparency
  panel (now with per-tool latency), save-to-logbook, and transcript
  export. Sidebar: eight one-click demo scenarios.
- **📊 Dashboard** — fleet health table (scored 0-100), parameter trend chart
  with warning/critical limit lines, and the delay Pareto with the named
  bottleneck (the "visualization dashboard" optional enhancement).
- **🚨 Alerts** — inbox of autonomously generated reports: ⚠️/🚨 reactive
  abnormal alerts AND 🔮 `PREDICT_*` predictive-failure reports (fired
  BEFORE any limit breach), each with one-click handoff to the agent;
  below it the **🔔 role-routed notification feed** (FR7) filterable by
  engineer / supervisor / planner / procurement.
- **🛠 Work Orders** — the auto-raised + agent-raised action ledger with
  status lifecycle (OPEN → IN_PROGRESS → DONE).
- **📡 Live Monitor** — manual mode of the simulated IoT feed: stream the next
  synthetic reading, watch the anomaly layers fire, hand the alert straight
  to the agent, one-click reset of the synthetic overlay (streamed readings
  never mutate `readings.csv`).
- **📒 Logbook** — persistent digital maintenance logbook with download (the
  "automatic digital logbook" optional enhancement).

The sidebar also supports **dynamic knowledge-base ingestion**: upload any
.md/.txt/.pdf manual or SOP (PDF text is extracted with per-page markers
for citation traceability) and the retrieval index rebuilds live — VULCAN can
cite it on the very next query.

## Repository layout

```
app.py                      Streamlit UI: chat + autopilot + alerts + WOs
cli.py                      Terminal chat (same orchestrator)
vulcan_service.py           v11 production service: own process, SIGTERM-
                            graceful, /healthz, fail-fast config
sentinel.py                 Headless autonomous monitor: reactive +
                            PREDICTIVE channels (cron twin, same logic)
vulcan/
  orchestrator.py           Agentic loop: plan → parallel tools →
                            synthesize; compaction, streaming, retries
  autopilot.py              Autonomous cycle: scan → reactive+predictive
                            alerts → notify roles → spares-checked auto
                            WO on CRITICAL → SLA watchdog
  daemon.py                 Server-side autonomy thread — AUTOSTARTS (v10),
                            optional budget-capped LLM diagnostics
  notify.py                 Role-routing matrix + JSONL ledger + webhook
                            (FR7 user-specific notifications)
  db.py                     SQLite WAL store: orders/notifs/state (v11),
                            auto-migrates v10 JSON files
  logging_setup.py          Structured JSON logs, rotation, key redaction
  metrics.py                Counters behind the /healthz endpoint
  retention.py              Cycle-time pruning (work orders never pruned)
  datastore.py              mtime-cached sensor store (0 disk reads warm)
  learning.py               Auto-injected confidence priors from feedback
  config.py                 Paths, model, v10 autonomy knobs (call-time)
  prompts/system_prompt.txt VULCAN v8 behavioral specification
  tools/
    retrieval.py            RAG: hybrid TF-IDF+BM25 chunk search
    anomaly.py              Layered detection: threshold/z-score/CUSUM/trend
    rul.py                  4 RUL models (honest INCALCULABLE paths)
    delay_analytics.py      Pareto, TBF trend, repeat offenders, bottleneck
    cmms.py                 Spares lookup + SQLite feedback persistence
    workorders.py           De-duplicated work-order ledger (auto + agent)
data/
  knowledge_base/           Manual extract, SOP, maintenance history (SYNTHETIC)
  sensor_data/              readings.csv + thresholds.csv (SYNTHETIC)
  delay_log.csv             Plant delay records (SYNTHETIC)
  spares.json               Mock CMMS catalog (SYNTHETIC)
ARCHITECTURE.md             System design, data flow, reasoning pipeline
samples/sample_queries.md   Sample inputs + expected output shapes
```

## Demo data

All bundled data is **synthetic** and labeled as such; VULCAN's Section-12
rules cap operational confidence on synthetic inputs and append a validation
banner. The seeded scenario: caster mold oscillator **CC2-MO-01** with
accelerating vibration (2.8 → ~7.4 mm/s over 12 days) against OEM limits of
7.0 mm/s warning / 11.0 mm/s trip, a maintenance history containing an
analogous confirmed bearing failure, one bearing in stock, and a delay log
where **HSM-COILER-01** is the chronic plant bottleneck.

## Extending to real plant data

Drop real files into the same slots — `data/knowledge_base/*.md` (manuals,
SOPs, history), `data/sensor_data/*.csv`, `data/delay_log.csv`,
`data/spares.json` — and restart. No code changes needed. For production:
swap TF-IDF for a vector DB, point `cmms.py` at the live CMMS API, and feed
`anomaly.py` from the historian; the tool schemas stay identical.

## Hybrid retrieval

Knowledge-base search fuses TF-IDF (bigram, phrase-sensitive) with BM25
(exact-term recall) via normalized weighted score fusion — both raw scores
are reported per chunk for full transparency, and queries irrelevant to
both retrievers return nothing rather than forced citations.

## Measured results (evaluation harness)

`python evals/run_evals.py` runs 35 deterministic checks — detection
accuracy on seeded faults, false-positive control on healthy assets, RUL
sanity with valid confidence intervals, delay-analytics correctness,
retrieval relevance, five fabrication-resistance probes (unknown
equipment, missing thresholds, invalid model inputs, nonexistent parts,
irrelevant queries must all yield honest failure states, never invented
values), Section-5.2 prioritization audits, and a regression/robustness
suite (multi-asset spares mapping, dead-sensor FROZEN_VALUE detection,
missing-threshold honesty). **Current score: 51/51** (incl. autonomy suite H and v10's suite I, proving the sentinel predicts the seeded LF1-HYD-01 failure before any limit breach, grants autonomous CRITICAL only on full evidence, never re-alerts unchanged conditions, and routes every event class to its roles). The harness needs no API key and is
CI-ready (non-zero exit on failure). Scorecard: `evals/results.md`.

A second, LLM-dependent suite (`python evals/run_agent_evals.py`, needs an
API key) asserts end-to-end agent behaviors: mandatory urgency line,
genuine tool planning, depth-ladder scoping, valid JSON mode, end-to-end
fabrication resistance on unknown assets, and refusal of interlock-bypass
requests. A pytest suite (`pytest tests/`) plus a GitHub Actions workflow
(`.github/workflows/ci.yml`) make all deterministic checks CI-enforced.

`finetune/` contains a complete scaffold for the problem statement's
domain-specific-model merit item: a dataset generator for an input-
classification SLM plus an honest LoRA training/evaluation recipe.

## Docker deployment

```bash
docker build -t vulcan .
docker run -p 8501:8501 -e ANTHROPIC_API_KEY=sk-ant-... vulcan
```

## Notes & limitations

- Requires internet access to the Anthropic API; model name is configurable
  (`ANTHROPIC_MODEL`) — check https://docs.claude.com/en/api/overview for
  current models.
- Four numeric RUL models are implemented as genuine tools: linear drift
  (with 80% CI), Weibull conditional, Arrhenius thermal-aging, and P-F
  interval. Remaining Section-7 branches (e.g. Paris-law crack growth) are
  applied as transparent reasoning when their inputs are supplied in-chat.
- The feedback store persists locally in `data/feedback.sqlite3`.
- This is decision support: a human engineer authorizes every action.

## Deliverables checklist (problem-statement Section 9)

| Deliverable | Where | Status |
|---|---|---|
| Working prototype source code | this repo | ✅ |
| Architecture / stack / data-flow / model design / alerting & prediction logic / assumptions | `ARCHITECTURE.md` | ✅ |
| Install, configure, run instructions | `README.md` §Quick start | ✅ |
| Sample input & output demonstration | `samples/sample_queries.md` | ✅ |
| Measured quality evidence | `evals/results.md` — **51/51**, `tests/` — 43/43 | ✅ |
| **Screen recording** | record using `DEMO_SCRIPT.md`, add MP4 to the ZIP | ⚠️ **YOU must record this before submitting** |
