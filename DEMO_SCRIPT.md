# DEMO_SCRIPT — shot list for the mandatory screen recording

⚠️ **Section 9 of the problem statement requires a screen recording.
It is NOT bundled here — you must record it before submitting.**
This script makes that a ~8-10 minute single take. Target ≤ 10 min,
1080p, voiceover or captions.

## Setup (off camera)
```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
streamlit run app.py
```

## Shot 1 — Framing (30 s)
Show README header. Say: decision-support agent for steel-plant
maintenance; LLM plans over genuine deterministic tools; nothing is
ever fabricated (constraint C-07).

## Shot 2 — Proof before demo (45 s)
Terminal: `python evals/run_evals.py` → **51/51**, then
`python -m pytest tests/ -q` → **43 passed**. Say: detection,
prediction, analytics, retrieval, fabrication-resistance,
Section-5.2 prioritization, regression/robustness, AND v10's
predictive autonomy + role routing (suite I), AND v11 production
readiness (suite J: WAL storage, storm guard, HMAC, fail-fast config)
are all measured, not claimed.

## Shot 3 — Reactive diagnosis (90 s)
Chat tab, demo scenario: *"CC2 mold oscillator vibration alarm —
diagnose."* Expand the tool-trace panel: show `detect_anomalies`
(4 layers fired), `search_knowledge_base` citations (manual + SOP +
history chunk IDs), confidence band in the answer.

## Shot 4 — Prediction (60 s)
Ask: *"How long until CC2-MO-01 vibration reaches the critical
limit?"* Show `estimate_rul` → point estimate + 80% CI + r². Then ask
an impossible one (*"RUL of GHOST-99"*) → **INCALCULABLE** with a
minimum-data plan. Say: honesty is a feature.

## Shot 5 — Section-5.2 prioritization (90 s)
Ask: *"What should the plant fix first and why?"* Show
`rank_maintenance_priorities`: ranked table, the four named criteria
(process criticality, delay severity, spares availability,
procurement lead time) each with raw value, weight, source, and
honesty flags for unmapped assets.

## Shot 6 — Proactive + real-time (75 s)
Dashboard tab: fleet health table + trend chart with limit lines +
delay Pareto/bottleneck. Live Monitor tab: stream a synthetic
reading, watch layers fire, click "Ask VULCAN about this alert".
Finish by clicking "Reset simulated readings" so the demo baseline
(and the 38/38 harness) stays pristine for re-runs.

## Shot 6b — AUTONOMY: the Autopilot (90 s) ⭐ headline shot
Sidebar: toggle **Autopilot ON** + "auto-advance the simulated IoT
feed", set the cycle to 10 s. Do nothing — narrate while the status
strip ticks by itself: a cycle streams a synthetic reading, the
WARNING fires, the strip turns red, the 🚨 Alerts tab fills with a
structured report, the 📒 Logbook gains entries, and when the trend
crosses the trip limit the 🛠 Work Orders tab shows an auto-raised
CRITICAL work order with its evidence reference — all with zero
clicks. Run a second cycle on unchanged data → no repeat alerts
(state-aware de-dup). Say: the system approaches the engineer, not
the other way round. Mention the headless twin
(`python sentinel.py --watch 300`) runs the SAME tested decision
function for cron/unattended deployment. Finish with "Reset simulated
readings" (Live Monitor tab) so the baseline stays pristine.

## Shot 7 — Knowledge + learning loop (60 s)
Sidebar: upload a new PDF/SOP → ask a question it alone can answer →
citation appears. Then record feedback (CONFIRMED) on a diagnosis, start a NEW
session, and ask the same diagnosis again — point out the raised
confidence band: the priors were auto-injected from the feedback
store (closed loop, no tool call needed). Optionally ask the agent
to raise a work order from the diagnosis → show it in 🛠 Work Orders.

## Shot 8 — Logbook + wrap (30 s)
Save an answer to the Logbook tab, download it. Close on the
architecture diagram in ARCHITECTURE.md.

## Shot 10 (v10) — ⭐⭐ ZERO-TOUCH PREDICTIVE AUTONOMY (90 s) — open with this
This is the shot that answers "efficient but not automated". Kill the
server, delete `data/alerts/`, `data/notifications.jsonl`,
`data/work_orders.json` (cold plant). Run **only**
`streamlit run app.py` — touch NOTHING. Narrate the status strip:
"the daemon autostarted with the server; I have not clicked anything."
Within one cycle:
1. 🚨 Alerts tab → a 🔮 `PREDICT_*` report on **LF1-HYD-01**: latest
   oil temp **79.3 °C, BELOW the 80 °C warning limit**, projected to
   hit the 95 °C critical limit in **~20 h** (80 % CI shown). Read the
   line "*No limit has been breached yet — this alert exists so the
   intervention can be PLANNED instead of forced.*"
2. 🛠 Work Orders tab → an auto-raised CRITICAL order titled
   "PLAN intervention — predicted critical…", details carrying the
   **live CMMS spares read** (SP-0009, qty=0, lead 28 d).
3. 🔔 Notifications (Alerts tab, bottom) → switch the role filter:
   **planner** sees PREDICTIVE_FAILURE, **procurement** sees
   PROCUREMENT_RISK (zero stock gates the repair), **supervisor** sees
   the work order. Say: FR7 — every autonomous decision reaches the
   role who must act on it.
Then ask the chat "what should the plant fix first" → the Section-5.2
prioritizer independently ranks LF1-HYD-01 #1. Close: "the system
predicted the failure, planned the work, checked the spares, and told
the right people — before a single limit was breached, with zero human
input."

## Recording checklist
- [ ] All shots captured, ≤ 10 min total (lead with Shot 10)
- [ ] Tool-trace panel visible at least twice (explainability evidence)
- [ ] 51/51 + 43 passed shown on screen
- [ ] Predictive alert shown with the latest value BELOW the warning limit
- [ ] Role filter switched at least twice in the 🔔 inbox
- [ ] Exported as MP4, added to the submission ZIP

## Shot 9 (v9) — Autonomy that survives a closed tab (45 s)
Sidebar → 🛰 Server-side daemon → **Start daemon**. Point at the status
line ("RUNNING · N unattended cycles"). Now CLOSE the browser tab, wait
two intervals, reopen the app: the cycle counter kept climbing and any
new alerts/work orders were raised while no UI existed — say out loud
that v8's fragment loop stopped the moment the tab closed, and this is
the fix. Then send a chat query and point at the FIRST tokens appearing
near-instantly (true streaming, not re-chunked text).


## Shot 11 (v11) — Production readiness in 60 seconds (optional but potent)
Terminal, three beats:
1. `python vulcan_service.py --once` — point at the STRUCTURED JSON log
   lines: one autonomous cycle → 5 alerts (1 predictive), 1 auto work
   order, 7 role-routed notifications, graceful stop with full metrics.
   Say: autonomy as its own process — this is what runs under systemd
   or Docker, not a browser thread.
2. `VULCAN_RUL_WARN_HOURS=5 python vulcan_service.py --once` →
   **refuses to start, exit 2**, plain-language error. Say: a
   misconfigured autonomous system must fail loud, not run wrong.
3. Open PRODUCTION.md briefly: failure-mode table + scaling path
   (SQLite→PostgreSQL, CSV→OPC-UA, /healthz→Prometheus). Say: every
   prototype piece has a named successor behind a single code seam —
   plant scale is an upgrade, not a rewrite.
