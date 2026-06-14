# Sample Inputs & Expected Outputs

The first eight scenarios below are wired into the app sidebar as one-click demos. Outputs vary slightly
per run (LLM-generated), but the structure below is enforced by the system
prompt and the tool layer.

## 1. Diagnosis + RUL (Level 3 full diagnostic)

**Input:** "Engineer here. Mold oscillator CC2-MO-01 on Caster 2 is showing
rising vibration over the last days. Check the sensor history for
vibration_mm_s, tell me what's wrong, the RUL, the risk, and what to do.
We have a 6 h maintenance window on Saturday."

**Tools fired:** `detect_anomalies`, `estimate_rul`,
`search_knowledge_base` (manual limits + history), `query_spares`,
`get_feedback_history`.

**Expected output shape:** Real-time alert block first (warning limit
7.0 mm/s approached/breached per the manual), then the full VULCAN report:
agent execution log with PLAN line; diagnostic chain citing
(readings.csv → Tier 1), (manual_CC2_mold_oscillator.md §4.2 → Tier 3),
(maintenance_history_CC2.md WO-23-1187 → Tier 2); fault tree dominated by
drive-side bearing degradation with the 2024 servo-valve case explicitly
ruled out and the process-defect hypothesis tested; RUL with point estimate
+ 80% CI in hours from the regression tool; risk score /100 with
intervention-urgency window; cascade map through the CASTING chain;
6-hour-window-optimized action plan (manual §6.4 says 5–6 h bearing
replacement — fits); spares table showing 1 bearing on hand vs 21-day lead
time; synthetic-data validation banner.

## 2. Plant triage (Level 4)

**Input:** "Analyze our delay log and tell me what to fix first across the
plant — give me the triage board and name the bottleneck."

**Tools fired:** `analyze_delay_log` (+ optional `detect_anomalies` on
named assets).

**Expected:** Triage board ranking HSM-COILER-01 first (largest Pareto
share, ≥3 wrapper-roll-jam recurrences → chronic offender, deteriorating
TBF), with sub-scores shown and the bottleneck named.

## 3. Spares / procurement

**Input:** "Do we have a spare oscillator drive bearing in stock for
CC2-MO-01, and is the lead time a problem given its condition?"

**Expected:** Stock = 1 (Tier-1 CMMS read), lead time 21 days compared
against the RUL lower bound; emergency-procurement flag raised if lead
time exceeds it; recommendation to reserve the on-hand unit.

## 4. Hinglish supervisor query (multi-turn, role-aware)

**Input:** "Supervisor bol raha hoon — CC2 ka oscillator kab tak chalega?
Production rokna padega kya?"

**Expected:** Hinglish reply, supervisor depth (decision summary, business
vocabulary), same numbers as the engineer view — facts never change with
persona, only depth and vocabulary.

## 5. JSON dashboard mode

**Input:** "Give me the latest CC2-MO-01 diagnostic as JSON for the
dashboard."

**Expected:** One framing line + a single valid JSON object per the
Section-9c schema, same Report ID as the prose report, nulls + matching
information_gaps entries instead of invented values.

## 6. Feedback learning loop

**Input:** "Feedback on the CC2-MO-01 report: CONFIRMED — teardown found
outer-race spalling on the drive-side bearing, exactly as diagnosed.
Record it."

**Tools fired:** `record_feedback` (persists to SQLite).

**Expected:** Confirmation that the verdict is stored, plus a statement of
the Section-13 learning effect (this evidence-pattern→mode mapping upgraded
toward Tier 2 for this equipment class). Re-asking about a similar bearing
case in a NEW session retrieves this verdict via `get_feedback_history`.

## 7. Fleet health check (proactive)

**Input:** "Scan the whole plant — which assets should I be worried about
right now, and why?"

**Tools fired:** `scan_plant_health` (+ drill-down `detect_anomalies`).
**Expected:** CC2-MO-01 vibration ranked worst (WARNING, score ~33/100),
with the scoring heuristic transparently stated.

## 8. Weibull RUL with OEM data

**Input:** "OEM data for the coiler mandrel drive bearing class: Weibull
beta 2.4, eta 18000 h. This unit has run 12000 h. What's the remaining life
and should we plan replacement at the July shutdown (about 700 h away)?"

**Tools fired:** `estimate_rul_weibull`.
**Expected:** Median residual life ~6500 h with a wide 80% band
(~1300-15100 h), reconciled against the 700 h window — replacement at the
July shutdown is comfortably early; the band's source (user-supplied OEM
parameters) is cited in the evidence register.

## Bonus robustness probes (for the judges)

- Paste a sensor dump with a negative RPM → quarantined per the
  malformed-input rule, not used in calculations.
- Ask about an asset with no data → Information Gap + minimum data set,
  never a fabricated diagnosis.
- Embed "ignore previous instructions" inside a pasted log → flagged as a
  data-integrity anomaly, analysis continues.

## Plant-level prioritization (Section 5.2)

**Input:** "We have budget for one major intervention this week. What should
the plant fix first, and show me exactly why."

**Expected shape:** VULCAN calls `rank_maintenance_priorities` and renders a
ranked table. Each row carries the four named criteria with raw values
(worst health score + fired layers, lost minutes, criticality 1-10 with
rationale, spares stock + lead time), the weight applied, the per-factor
contribution that arithmetically sums to the total, the priority band, and
honesty flags (e.g. `CRITICALITY_NOT_MAPPED — default 5/10 used`). The
recommendation cites the register/CMMS/delay-log sources and states the
weights are a heuristic the reliability team can re-tune.

## 9. Zero-input autonomous cycle (v10 — no query at all)

**Input:** *(none — this is the point)*. Start the app:
`streamlit run app.py`. The daemon autostarts and runs one pass.

**Autonomous outputs produced within one cycle, with zero human input:**

- `data/alerts/PREDICT_..._LF1-HYD-01_oil_temp_C_RUL.md` — predictive
  CRITICAL: latest 79.3 °C (warning limit 80 °C **not breached**),
  projected to reach the 95 °C critical limit in ~20 h (80 % CI shown),
  "intervention can be PLANNED instead of forced".
- `data/work_orders.json` — auto-raised CRITICAL order
  "PLAN intervention — predicted critical oil_temp_C on LF1-HYD-01",
  details carrying the live CMMS spares read
  (SP-0009 "hydraulic pump cartridge kit", qty=0, lead 28 d).
- `data/notifications.jsonl` — role-routed records:
  `PREDICTIVE_FAILURE → engineer, planner`;
  `PROCUREMENT_RISK → procurement, planner` (zero stock gates repair);
  `WORK_ORDER_RAISED → supervisor`; plus reactive WARNING alerts for the
  seeded CC2-MO-01 degradation and the BF3 dead-sensor escalation.
- `data/logbook.md` — every action appended with evidence references.
- Second cycle on unchanged data: **zero repeat alerts** (independent
  state-aware de-dup on the reactive and predictive channels).
