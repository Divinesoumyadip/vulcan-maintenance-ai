# VULCAN — System Architecture & Design Document

Tata Steel AI Hackathon 2026 · Round 2 — Agentic AI Challenge
Solution: Maintenance Wizard for Industrial Equipment

## 1. System architecture

VULCAN is a tool-augmented LLM agent. The intelligence is split deliberately
between two layers, each doing what it is best at.

The **behavioral layer** is an 812-line system prompt (v7.0,
`vulcan/prompts/system_prompt.txt`) that defines twelve reasoning agents
(A1 parser/planner through A12 logbook), a response-depth ladder so output
matches input, evidence tiers with calibrated confidence bands, plant
topology chains for cascade analysis, role-aware rendering
(engineer/supervisor/procurement/safety), JSON serialization mode, and 23
inviolable constraints — chief among them C-07: never fabricate a value.

The **computational layer** is a set of deterministic Python tools the LLM
can genuinely invoke: `search_knowledge_base` (hybrid RAG — TF-IDF + BM25 with normalized
weighted score fusion — over manuals, SOPs and history with chunk-level
provenance), `detect_anomalies` (a four-layer
engine: threshold breach, z-score deviation, CUSUM drift, trend rate),
`estimate_rul` (linear-regression extrapolation to the critical limit with
an 80% confidence interval derived from slope standard error),
`estimate_rul_weibull` (conditional residual life from user/OEM-supplied
β, η and age — never assumed), `estimate_rul_arrhenius` (thermal-aging
life from supplied Ea and temperatures) and `estimate_rul_pf_interval`
(remaining P-F window — together covering four of the six Section-7 model
branches numerically), `scan_plant_health` (fleet-wide health
scores powering proactive triage and the dashboard),
`analyze_delay_log` (Pareto ranking, time-between-failure trends, chronic
repeat offenders, bottleneck identification),
`rank_maintenance_priorities` (the Section-5.2 prioritizer: weighted fusion
of process criticality, delay severity, spares availability and procurement
lead time plus live condition risk — every sub-score reported with raw
value, weight and data source, defaults always flagged), `query_spares` (CMMS catalog
lookup), and `record_feedback`/`get_feedback_history` (SQLite-persisted
learning loop).

The orchestrator (`vulcan/orchestrator.py`) runs the agentic cycle: the LLM
reads the query, plans which tools the input requires, the host executes
those calls and returns real results, and the loop repeats (up to a
configurable round cap) until the model produces its final synthesized
answer. A runtime-context system block injects the true current datetime so
report IDs never contain an invented date.

## 2. Technology stack

Python 3.10+; Anthropic Messages API with native tool use (model
configurable, default `claude-sonnet-4-5`); Streamlit chat UI plus a CLI;
scikit-learn TF-IDF for retrieval; pandas/numpy/scipy for analytics and
statistics; SQLite for feedback persistence. Everything besides the LLM API
runs locally with no GPU, so the prototype is reproducible on any laptop.

## 3. Data flow and system flow

User query → Streamlit/CLI → orchestrator builds the message with the
system prompt (prompt-cached) and runtime context → LLM plans (A1) →
emits tool_use blocks → orchestrator executes the Python tools against
`data/` → JSON results return as tool_result blocks → LLM integrates them
as tiered evidence (sensor reads Tier 1, history Tier 2, manuals Tier 3,
SOPs Tier 4) → final answer rendered at the appropriate depth level, with
a tool-trace transparency panel in the UI showing every genuine call.

Knowledge ingestion is file-drop simple: markdown/text into
`data/knowledge_base/` (chunked ~900 chars and TF-IDF indexed at startup),
CSVs into `data/sensor_data/` and `data/delay_log.csv`, catalog records
into `data/spares.json`.

## 4. Model design and reasoning pipeline

Reasoning follows the system prompt's orchestration rule: A1 classifies the
input type(s), scores completeness 0–100, and states a plan naming which
agents run and which are skipped with reasons. Diagnosis (A4) builds a
fault-probability tree summing to 100% with at least one ruled-out cause
and an explicit upstream process-defect hypothesis test. Prediction (A5)
binds to the implemented RUL tool when sensor history exists, and returns
an honest INCALCULABLE state with a minimum-data plan when it does not.
Risk (A6) scores four 25-point components and traces cascades through the
named plant chain with per-link probabilities, flagging any
catastrophic-class path regardless of likelihood. Actions (A7) are tiered
immediate/short/long-term, each with responsible role, permits (LOTO etc.),
acceptance criteria, and escalation-if-skipped, and can be sequenced into a
stated outage window.

Confidence is calibrated, not stylistic: bands are tied to evidence-tier
combinations (e.g., >74% requires Tier-1 or Tier-2 support), synthetic
inputs cap operational confidence at 65% while methodological confidence is
stated separately, and conflicts between sources are logged rather than
silently resolved.

## 4b. Autonomy model — agentic, not merely automated

Two operating modes share one brain. INTERACTIVE: a human asks; the LLM
autonomously plans which tools to chain over multiple rounds (the agentic
loop). AUTONOMOUS: `sentinel.py` needs no human at all — it scans every
monitored (equipment, parameter) pair on a schedule, compares against its
persisted severity state, and on any NEW or ESCALATED condition writes a
structured abnormal-alert report to `data/alerts/`, logs it to the digital
logbook, and (with `--with-llm`) invokes the full agent to attach a complete
diagnostic — so engineers are approached BY the system, not the reverse.
Since v10 this loop AUTOSTARTS with the server (no click, no terminal) and
runs a second, PREDICTIVE channel: the RUL trend is projected every pass,
and a failure forecast inside the configured horizon alerts BEFORE any
limit is breached, routing role-specific notifications and raising a
spares-checked work order so the intervention is planned, not forced.
State-aware dedup means an unchanged WARNING alerts once (no alarm fatigue),
escalations always re-alert, and recoveries are logged RESOLVED. Authority
stays bounded: the sentinel reads sensor data and writes reports; it never
creates data and never executes physical actions — autonomy of analysis,
human authority over intervention.

## 5. Alerting and prediction logic

Anomaly layers fire in order against stored readings: configured
warning/critical limits first, then statistical deviation (|z| ≥ 3 vs a
baseline window), then CUSUM drift (k = 0.5σ, h = 5σ), then recent-window
trend slope. Any threshold breach or catastrophic-path finding triggers the
Section-10 real-time alert format — a sub-12-line scannable block emitted
before the long-form analysis and routed by persona (safety exposure →
Safety + Supervisor; emergency procurement → Procurement + Supervisor).
Prediction extrapolates the regression trend to the critical threshold;
the 80% CI comes from the t-distributed slope error, and a CI bound that
includes zero slope is reported as "unbounded" rather than a fake number.
Emergency procurement is flagged whenever estimated lead time exceeds the
RUL lower bound. v10 moves this prediction INTO the autonomous loop: the
sentinel evaluates the linear-drift RUL for every pair on every pass and
fires predictive WARNING/CRITICAL alerts on its own de-dup channel when
the projected crossing falls below the 72 h / 24 h horizons (§14).

## 6. Feedback-driven improvement

Engineer verdicts (CONFIRMED/PARTIAL/INCORRECT) persist to SQLite via a
tool call, so learning genuinely survives sessions. On later diagnoses of
the same equipment class, the model retrieves this history (Tier-2
evidence) and applies the Section-13 schema: confirmed mappings may start a
band higher, incorrect ones start a band lower and must surface the
previously missed cause.

## 6b. Optional enhancements implemented

All six optional enhancements from the problem statement are present:
conversational interface (Chat tab), visualization dashboard (health table,
trend chart with limits, delay Pareto), simulated IoT integration (Live
Monitor tab — streamed readings go to a separate synthetic overlay file, trigger the
layered alert engine, can be handed to the agent in one click, and a reset
button restores the pristine demo baseline), dynamic per-equipment knowledge base
(sidebar ingestion with live reindex), automatic digital logbook
(persistent data/logbook.md with save/download), and user-role-based
alerts/recommendations (Section-11 persona rendering and routing). The
live-feed simulator is deliberately NOT an LLM tool: the agent can read
sensor data but can never create it — honesty by construction.

## 6c. Quality assurance

A deterministic 38-check evaluation harness (`evals/run_evals.py`) measures
detection accuracy, false-positive control, prediction sanity, analytics
correctness, retrieval relevance, and — most importantly — fabrication
resistance and regression/robustness probes: they verify that missing or invalid data always produces
an honest failure state (NO_DATA / INCALCULABLE / NO_MATCH / INVALID_INPUT)
rather than an invented value, enforcing constraint C-07 at the code level.
During development the harness caught and drove fixes for three real
defects: a substring-matching bug in the spares lookup that returned false
part matches, a CUSUM false positive on low-noise healthy assets (resolved
by retuning k to 1.0σ and h to 8σ for short series), and a retrieval-ranking
assumption. The regression suite additionally locks in fixed defects (the ';'-list
spares-compatibility mapping, dead-sensor FROZEN_VALUE flagging, and the
missing-threshold INCALCULABLE path). Current score: 38/38, runnable
without an API key, CI-enforced together with the 10 pytest checks (which
also pin the prompt-cache block ordering).

## 7. Assumptions and limitations

All bundled data is synthetic and labeled; the system enforces its own
validation-required banner on synthetic-based outputs. The implemented
numeric RUL model is linear drift; Weibull/Arrhenius/Paris/PF-interval are
applied as transparent reasoning when users supply their inputs. TF-IDF
retrieval is intentionally lightweight — production would substitute a
vector database without changing tool schemas. The system is
decision-support by design: it analyzes autonomously but a human engineer
authorizes every action, and irreversible or safety-critical steps are
explicitly flagged as requiring authorization. Internet access to the LLM
API is required; per-query latency is a few seconds to ~30 s depending on
tool rounds.

## 8. Installation, configuration, run

See README.md. In short: `pip install -r requirements.txt`, export
`ANTHROPIC_API_KEY`, then `streamlit run app.py` (or `python cli.py`).
Sample inputs and expected output shapes are in
`samples/sample_queries.md`; the UI sidebar loads eight demo scenarios with
one click.

## 12. v8 addendum — autonomy & efficiency engineering

This release answers two specific review findings on v7: *"the system is
not automated enough"* and *"it is inefficient"*. Both fixes are
structural, tested, and measured.

### 12.1 The autonomy gap, closed

v7's autonomy lived in `sentinel.py` — genuinely unattended, but only if a
human started it in a terminal, and its outputs (alert files) never
surfaced in the product. v8 introduces `vulcan/autopilot.py`, an in-app
autonomous cycle driven by a Streamlit auto-rerunning fragment:

```
every N seconds (no human action):
  [demo only] advance simulated IoT feed (synthetic, reversible overlay)
  sentinel_pass()          ← the SAME tested decision function as the CLI
    ├─ NEW/ESCALATED  → write structured alert report (data/alerts/)
    │                   → append digital logbook
    │                   → if CRITICAL: create_work_order()  [de-duplicated]
    └─ RECOVERED      → RESOLVED logbook notice
  persist severity state (sentinel_state.json)
```

Design decision: the autopilot **reuses** `sentinel_pass()` rather than
re-implementing it, so the in-app loop, the cron-able CLI loop, and the
test suite all exercise one body of decision logic. The work-order ledger
(`vulcan/tools/workorders.py`) closes alert → action automatically and is
simultaneously exposed to the agent as three tools (create/list/update),
so conversational diagnoses produce tracked tasks too. Idempotency: while
an order is OPEN/IN_PROGRESS on an asset/parameter, repeat triggers return
the existing order — the same de-duplication philosophy as alerting.

The feedback loop is also now closed *automatically*: `vulcan/learning.py`
aggregates the SQLite verdict store into a compact priors block injected
into the system context on every turn. Confirmed failure modes raise the
agent's confidence band; refuted ones lower it — without the agent having
to decide to look, and at a cost of a few hundred tokens only once
feedback exists.

### 12.2 The efficiency gap, closed (measured)

| Issue in v7 | v8 fix | Measured effect |
|---|---|---|
| Every tool call re-read **every** sensor CSV; `scan_plant_health` did it once **per pair** (O(pairs × files) disk I/O per scan, repeated on every dashboard rerun and sentinel pass) | `vulcan/datastore.py`: one in-memory load, invalidated by an mtime+size fingerprint | **0 disk reads per warm scan** (v7: ≥ 8 on the demo set, growing with plant size); warm fleet scan ≈ 12 ms; regression-tested (`test_fleet_scan_uses_single_cached_load`) |
| Tool calls executed serially | Thread-pool execution of all tool calls in a round | latency of a multi-tool round ≈ slowest tool, not the sum |
| Conversation context grew unbounded — stale RAG chunks/fleet scans paid tokens every turn | Compaction: tool results older than 2 turns shrink to one-line stubs (assistant prose untouched; model told it may re-run a tool for fresh detail) | per-turn token cost flat over long sessions; tested (`test_history_compaction_replaces_old_tool_results`) |
| Blank spinner during generation | `ask_stream()` generator + `st.write_stream` | first tokens render immediately |
| Raw `401 invalid x-api-key` dumped into chat | `anthropic.AuthenticationError` mapped to an actionable message + sidebar one-click key validation | misconfiguration is diagnosed in-product |
| No retry on transient API failures | exponential backoff (2s→4s→8s) on connection/timeout/rate-limit/5xx | demo survives flaky networks |

Cache-correctness note: freshness semantics are unchanged — any write to
`data/sensor_data/` (UI ingestion, live-stream overlay, external drop-in)
changes the fingerprint and forces a reload on next access. The datastore
has **no write path**, preserving the v7 invariant that nothing in the
agent's reach can create sensor data.

### 12.3 Tool inventory after v8

15 genuine tools: the 12 of v7 plus `create_work_order`,
`list_work_orders`, `update_work_order`. Registry/schema parity is
test-enforced (`test_orchestrator_registry_consistent`).

## 13. v9 — Stringency audit (claims re-verified against the code)

v9 is a hostile self-review of v8: every "automated" and "efficient" claim
was tested against what the code actually does. Six defects were found,
fixed, and pinned with regression tests.

| Defect found in v8 | v9 fix | Regression test |
|---|---|---|
| **Cosmetic streaming**: `ask_stream` made a blocking `messages.create` call, then re-sliced the finished text into 80-char chunks — "token-by-token" was a UI illusion | True streaming via `client.messages.stream`; every server-emitted delta reaches the UI immediately. Retries are duplication-safe: exponential backoff only **before** the first surfaced token; a mid-stream failure raises instead of replaying shown text | `test_streaming_is_genuine_not_rechunked` |
| **Tab-bound "autonomy"**: the in-app Autopilot is a Streamlit fragment, which executes only while a browser session is open — close the tab and monitoring stops | `vulcan/daemon.py`: a daemon thread in the **server process** (`SentinelDaemon`, process-wide singleton) keeps scanning/alerting/raising work orders with zero tabs open. The fragment is demoted to a live viewer of autonomous activity | `test_daemon_lifecycle` |
| **Double anomaly computation**: each sentinel pass ran the layered engine inside `scan_plant_health`, then *again* per asset — 2× compute per pass, per UI cycle | `scan_plant_health(include_detections=True)` returns its detections; `sentinel_pass` reuses them. Kept off for the LLM tool path to avoid doubling tool-result tokens | `test_sentinel_runs_anomaly_engine_once_per_pair` |
| **Dead sensors ignored by autonomy**: FROZEN_VALUE data-quality flags were computed, then discarded by the sentinel's severity logic | A flatlined-but-"NORMAL" pair escalates to a WARNING alert (`dq_escalated`) — an unmonitored asset is itself an abnormal condition. The alert report names the escalation reason | `test_dead_sensor_escalates_to_autonomous_warning` |
| **Escalations swallowed by WO de-dup**: an open MEDIUM order absorbed a later CRITICAL trigger silently | `create_work_order` returns `ESCALATED`: the open order's priority is bumped, an `escalation_note` records old → new + evidence ref. Lower/equal priority still de-duplicates quietly | `test_work_order_priority_escalates_not_swallowed` |
| **Learning over-fit a single anecdote**: one CONFIRMED verdict moved a confidence band | Minimum-evidence gate (`MIN_EVIDENCE = 2`): n=1 yields "insufficient evidence — keep confidence unchanged, mention as anecdote" | `test_learning_prior_requires_minimum_evidence` |

### 13.1 Robustness hardening (same audit)

- **Atomic state writes** (`save_state`, work-order `_save`): tmp + rename.
  v8's direct `write_text` could leave a half-written JSON on crash; for the
  sentinel that meant all de-dup state silently reset — every alert
  re-fires (alarm storm). Tested: `test_sentinel_state_save_is_atomic`.
- **Single-flight ticks**: `autopilot_tick` takes a non-blocking lock; the
  server daemon, the UI fragment, and multiple browser sessions can never
  run overlapping passes against shared state — late callers skip and say
  so. Tested: `test_autopilot_tick_is_single_flight`.
- **Call-time path resolution**: `load_state`/`save_state` previously bound
  `STATE_PATH` as a *default argument* (frozen at import), which defeated
  test monkeypatching and let the test suite leak sentinel state and
  logbook entries into the live `data/` directory. Paths now resolve at
  call time; the leaked artifacts were removed from the repo.
  Tested: `test_sentinel_paths_resolve_at_call_time`.
- **Rerun-proof dashboard**: Streamlit re-executes the whole script on
  every interaction; v8 therefore re-ran the full fleet scan + delay
  Pareto on every rerun. v9 caches both behind `st.cache_data` keyed on
  the public `datastore.fingerprint()` — recomputed only when data
  actually changes. The Alerts inbox also reads each report file once,
  not twice.

### 13.2 Test inventory after v9

At the time of v9: 25 pytest tests + 38 deterministic eval checks
(suites A–H), all passing, no API key required. (Superseded — see §14.3.)

## 14. v10 — Zero-touch, predictive, role-routed autonomy

v10 is a hostile audit of v9's *autonomy* story specifically, prompted by
the recurring review verdict "efficient, but not automated". The finding
was accepted as correct: v9's automation was **opt-in** (a human had to
click *Start daemon* — the shipped default state of the product was
"plant unmonitored") and **reactive-only** (the sentinel alerted after a
limit or statistical layer had fired; it never *predicted* a failure on
its own, despite four RUL models sitting one tool-call away). Six gaps
were closed, each pinned by a regression test.

| Gap found in v9 | v10 fix | Regression test |
|---|---|---|
| **Autonomy was opt-in**: the daemon required a human click; default state was "unmonitored" | `ensure_autostarted()` launches the daemon with the server (`VULCAN_DAEMON_AUTOSTART=1` by default; interval `VULCAN_DAEMON_INTERVAL`). Idempotent per Streamlit rerun; sidebar buttons remain as a manual override | `test_daemon_autostart_honors_env` |
| **Autonomy was reactive-only** (FR5 unmet autonomously): alerts fired only after a layer tripped | The sentinel now runs **two channels** per pair per pass: reactive (anomaly layers) and **predictive** — `estimate_rul` projected to the critical limit. RUL < `VULCAN_RUL_WARN_HOURS` (72) → autonomous WARNING; < `VULCAN_RUL_CRIT_HOURS` (24) → autonomous CRITICAL, **before any limit is breached**. Own de-dup key (`<pair>::RUL`) so the channels escalate/resolve independently. Stringency: an INDICATIVE_ONLY (3–4 point) trend is capped at WARNING — autonomous CRITICAL requires full evidence | `test_predictive_alert_fires_before_any_limit_breach`, `test_predictive_critical_requires_full_evidence` |
| **FR7 (user-specific notifications) was entirely missing**: autonomous output ended at files + a UI inbox | `vulcan/notify.py`: explicit role-routing matrix (CRITICAL → engineer+supervisor; predicted failure → engineer+planner; procurement risk → procurement+planner; SLA breach → supervisor+planner; dead sensor → engineer). Append-only JSONL ledger; optional fail-safe webhook (`VULCAN_WEBHOOK_URL`) for Slack/Teams/SMS fan-out. Role-filtered 🔔 inbox in the Alerts tab | `test_autopilot_routes_role_notifications`, `test_notification_router_persists_and_filters_by_role` |
| **Auto work orders were spares-blind** (Section 5.2 half-closed) | Every autopilot-raised order carries a live CMMS read (item, on-hand qty, lead time) in its details; a zero-stock compatible spare on a CRITICAL asset fires PROCUREMENT_RISK. No catalog match is stated as an Information Gap, never guessed around | `test_predictive_critical_auto_raises_spares_checked_wo` |
| **No SLA escalation**: a CRITICAL order could sit OPEN forever silently | Per-tick watchdog: OPEN CRITICAL orders older than `VULCAN_WO_SLA_MIN` (60) trigger a one-time WORK_ORDER_SLA_BREACH escalation, de-duplicated via a `_sla_notified` bookkeeping key in the sentinel state | `test_wo_sla_breach_notifies_exactly_once` |
| **Daemon stopped at the alarm**: only the CLI sentinel (`--with-llm`) could attach diagnostics | Opt-in `VULCAN_DAEMON_LLM=1`: the daemon appends a full agent diagnostic to each CRITICAL alert file, hard-capped at `VULCAN_DAEMON_LLM_MAX_PER_HOUR` (4) so unattended autonomy can never burn an unbounded token budget; failures are recorded on the cycle summary, never fatal | covered by daemon lifecycle + budget logic (`_llm_budget_ok`) |

### 14.1 Hardening (same audit)

- **Double-driver fix**: v9 let the server daemon and the in-tab fragment
  drive ticks simultaneously if the Autopilot toggle was also on (the
  single-flight lock prevented overlap, but the demo stream advanced at
  double cadence and logs interleaved). With the daemon running, the
  fragment is now unconditionally a viewer.
- **Stale-state pruning**: severity keys for pairs no longer present in
  the data (decommissioned asset, renamed parameter) are dropped each
  pass; `_`-prefixed bookkeeping keys are preserved.
  Tested: `test_stale_state_keys_are_pruned_but_bookkeeping_survives`.
- **Atomic alert writes**: alert reports now use the same tmp+rename
  policy as the state file and the work-order ledger; predictive reports
  get distinct `PREDICT_*` filenames and a 🔮 badge in the inbox.
  Tested: `test_alert_write_is_atomic_and_predictive_named`.
- **Call-time configuration**: every v10 knob is read at call time
  (functions in `vulcan/config.py`), so operators and tests can change
  behavior without restarting the process.

### 14.2 Seeded demonstration of the predictive channel

`LF1-HYD-01 / oil_temp_C` (ladle-furnace hydraulic power unit,
criticality 8/10): 12 readings rising ~0.8 °C/h, latest **79.3 °C — below
the 80 °C warning limit**, projected to reach the 95 °C critical limit in
**~20 h** (80 % CI 19.3–20.0 h). Its only compatible spare (SP-0009) has
**zero stock and a 28-day lead time**. One cold autonomous cycle therefore
demonstrates the entire v10 chain with no human input: predictive
CRITICAL alert (`PREDICT_*` report) → spares-checked CRITICAL work order
("PLAN intervention…") → PREDICTIVE_FAILURE routed to engineer+planner →
PROCUREMENT_RISK routed to procurement — while every limit is still
green. The Section-5.2 prioritizer independently ranks this asset #1.

### 14.3 Test inventory after v10

34 pytest tests + 45 deterministic eval checks (suites A–I; suite I covers
predictive autonomy and role routing), all passing, no API key required.
The test suite is isolation-audited: it leaves the repository's `data/`
directory untouched.

## 15. v11 — Production hardening ("make it production-ready", taken literally)

v11 answers the review verdict "good prototype, not production-ready" the
same way v9/v10 answered theirs: name the gaps precisely, close each one,
pin each with a test, and be honest about what remains. Test inventory
after v11: **43 pytest tests + 51 deterministic eval checks** (new suite
J: production readiness), all passing, repo left untouched by the suite.

| Production gap | v11 fix | Pinned by |
|---|---|---|
| **Flat-file state**: whole-file JSON rewrites, no transactions, no indexed queries, no cross-process story | `vulcan/db.py` — single SQLite database in **WAL mode** holding work orders, notifications, and sentinel de-dup state; transactional escalations; indexed open-order lookups; busy-timeout writers; **lossless auto-migration** of v10 files (renamed `*.migrated`) | `test_db_is_wal_and_migrates_legacy_files`, J.production evals |
| **Autonomy tied to the web server**: the daemon thread dies with Streamlit and shares its resources | `vulcan_service.py` — autonomy as its **own process**: SIGTERM/SIGINT finish the in-flight cycle and exit 0; `--once` cron mode; same test-pinned `autopilot_tick` (one autonomy semantics, three run modes) | `test_service_once_mode_runs_single_cycle` |
| **Unsupervisable**: no way to ask a headless loop "are you alive, what have you done, what failed" | `vulcan/metrics.py` + **GET /healthz** (port `VULCAN_HEALTH_PORT`): uptime, cycles, alerts (incl. predictive), work orders, SLA breaches, storms, errors, last-cycle timestamp — wire to k8s liveness / Grafana / any uptime checker | `test_service_health_endpoint_serves_metrics` |
| **Print-style logging, secret-blind** | `vulcan/logging_setup.py` — structured **JSON lines** (stdout + rotating file 5 MB × 3), level/dir env-configurable, and a formatter that **actively redacts** Anthropic-key patterns | `test_secrets_never_reach_log_lines` |
| **Alarm-flood naivety**: 200 simultaneous breaches would mean 200 files + 200 notifications + dozens of auto work orders | **Alert-storm guard** (`VULCAN_MAX_ALERTS_PER_CYCLE`, default 25): above the cap, ONE roll-up report + ONE CRITICAL notification, per-event pipeline suppressed for the cycle, de-dup state still advanced — a storm is a systemic event, not N independent faults | `test_alert_storm_rolls_up_instead_of_flooding` |
| **Unbounded growth**: alert files and notification rows accumulate forever | `vulcan/retention.py`, called every cycle: prunes alert files + notification rows older than `VULCAN_RETENTION_DAYS` (default 30; 0 disables). **Work orders are never pruned** — they are the audit trail | `test_retention_prunes_old_artifacts_but_never_work_orders`, `test_retention_zero_disables` |
| **Unauthenticated alert sink**: a forged webhook POST could inject a fake CRITICAL or fake all-clear downstream | **HMAC-SHA256 signing** (`VULCAN_WEBHOOK_SECRET` → `X-Vulcan-Signature` header) on every webhook POST; deterministic, secret- and payload-bound | `test_webhook_hmac_signature_is_stable_and_secret_bound` |
| **Config silently degrades**: a clamped getter hid an out-of-range interval; inverted RUL horizons would run nonsense quietly | `validate_config()` — **fail-fast startup validation** on raw env values; the service refuses to start (exit 2) with plain-language errors | `test_config_validation_fails_fast_on_nonsense` |
| **Root container, demo Docker** | Non-root image (`uid 10001`, writes only `/app/data`), healthchecks, and `docker-compose.yml` that runs autonomy as its **own service** (UI demoted to a stateless viewer, `VULCAN_DAEMON_AUTOSTART=0`) sharing one data volume | Dockerfile / compose review |
| **Test-suite hygiene at the storage layer** | `tests/conftest.py` autouse fixtures give every test an isolated database and log directory — structurally impossible for the suite to touch `data/vulcan.db` | suite leaves `data/` byte-identical |

§8 of PRODUCTION.md states the honest scaling path: every prototype
component (SQLite → PostgreSQL/TimescaleDB, CSV ingest → historian/OPC-UA,
in-process metrics → Prometheus, webhook → Kafka, TF-IDF → pgvector) has
a named drop-in successor behind a single seam, chosen so plant-scale is
an upgrade, not a rewrite.
