# VULCAN Evaluation Scorecard

**Result: 51/51 checks passed**

| Suite | Check | Result | Note |
|---|---|---|---|
| A.detection | seeded degrading asset triggers threshold layer | ✅ PASS | fired=['L1_THRESHOLD', 'L2_ZSCORE', 'L3_CUSUM', 'L4_TREND'] |
| A.detection | statistical layer (z-score) also fires | ✅ PASS |  |
| A.detection | drift layer (CUSUM) also fires | ✅ PASS |  |
| A.detection | healthy asset raises no warning/critical layer (false-positive check) | ✅ PASS | fired=[] |
| A.detection | fleet scan ranks the seeded fault worst | ✅ PASS | worst=CC2-MO-01/vibration_mm_s |
| B.prediction | linear RUL produced with usable history | ✅ PASS |  |
| B.prediction | RUL point estimate engineering-plausible (24h-2000h for this seeded trend) | ✅ PASS | point=293.0 h |
| B.prediction | 80% CI present and brackets the point estimate | ✅ PASS | ci=['260.2 h', '330.0 h'] |
| B.prediction | regression fit quality reported (r²) | ✅ PASS | r2=0.932 |
| B.prediction | Weibull conditional RUL: band ordered and contains the median | ✅ PASS | median=6522.2 band=[np.float64(1294.8), np.float64(15145.3)] |
| C.analytics | bottleneck identified from delay log | ✅ PASS | got=HSM-COILER-01 |
| C.analytics | Pareto shares sum to ~100% | ✅ PASS |  |
| C.analytics | chronic repeat offender (>=3 recurrences) flagged | ✅ PASS |  |
| C.analytics | deteriorating TBF trend detected on bottleneck | ✅ PASS |  |
| D.retrieval | manual within top-2 for limits query (reaches agent context) | ✅ PASS | top2=['sop_vibration_monitoring.md', 'manual_CC2_mold_oscillator.md'] |
| D.retrieval | history surfaced for past-failure query | ✅ PASS | top_types=['history', 'manual'] |
| D.retrieval | SOP surfaced for permits query | ✅ PASS |  |
| E.honesty | unknown equipment → NO_DATA, never invented readings | ✅ PASS |  |
| E.honesty | RUL without data → INCALCULABLE + minimum-data plan | ✅ PASS |  |
| E.honesty | invalid Weibull inputs rejected, not guessed | ✅ PASS |  |
| E.honesty | nonexistent part → NO_MATCH, never a fake part number | ✅ PASS |  |
| E.honesty | irrelevant query yields no/low-confidence chunks (no forced citation) | ✅ PASS | n=0 |
| F.priority | prioritizer runs and ranks assets | ✅ PASS | n=6 |
| F.priority | top priority is a seeded problem asset (LF1-HYD-01 predicted-critical w/ zero spare stock, degrading CC2-MO-01, or bottleneck HSM-COILER-01) | ✅ PASS | top=LF1-HYD-01 score=55.9 |
| F.priority | ranking is monotonic non-increasing | ✅ PASS |  |
| F.priority | all four Section-5.2 criteria present per asset, each with raw value + source (explainability) | ✅ PASS |  |
| F.priority | weighted contributions reproduce the total score (arithmetic audit) | ✅ PASS |  |
| F.priority | every score bounded 0-100 with a priority band | ✅ PASS |  |
| F.priority | defaulted criticality is always flagged, never silent (honesty under missing data) | ✅ PASS | defaulted=['UTIL-HPU-03', 'RHF-01'] |
| G.regression | ';'-listed spare maps to EVERY compatible asset (UTIL-HPU-03 sees the shared servo valve) | ✅ PASS | detail={'items_listed': 1, 'items_in_stock': 0, 'min_lead_time_days': 45, 'flag': 'STOCK_OUT'} |
| G.regression | shared servo-valve STOCK-OUT visible to the prioritizer (exposure reflects 45-day lead) | ✅ PASS | exposure=100.0 |
| G.regression | spares search surfaces the stocked-out servo valve with its true qty (0) — no optimistic invention | ✅ PASS |  |
| G.robustness | flatlined series raises FROZEN_VALUE data-quality flag (dead-sensor detection feeds A1) | ✅ PASS | flags=['FROZEN_VALUE: last readings are flatlined — possible dead sensor'] |
| G.robustness | flatlined-but-in-range series fires no warning/critical layer (no false alarm on a dead sensor) | ✅ PASS |  |
| G.robustness | RUL with readings but NO configured threshold → INCALCULABLE + plan, never an invented limit (C-07) | ✅ PASS | status=INCALCULABLE |
| H.autonomy | sentinel autonomously detects the seeded degrading asset with NO human query (proactive, not approached) | ✅ PASS | alerts=['BF3-GCP-FAN-02::motor_current_A', 'CC2-MO-01::bearing_temp_C', 'CC2-MO-01::vibration_mm_s', 'LF1-HYD-01::oil_temp_C', 'LF1-HYD-01::oil_temp_C::RUL'] |
| H.autonomy | unchanged condition re-alerts ZERO times (state-aware dedup — no alarm fatigue) | ✅ PASS | second_pass_alerts=0 |
| H.autonomy | autonomous alert report carries layered Tier-1 evidence + severity transition + C-07 provenance note | ✅ PASS |  |
| I.predictive | sentinel fires a PREDICTIVE alert from the RUL horizon (FR5: failure predicted autonomously) | ✅ PASS | predictive_alerts=['LF1-HYD-01::oil_temp_C::RUL'] |
| I.predictive | predictive alert fires BEFORE any limit breach (latest value is still below the warning limit) | ✅ PASS | latest=79.3 warn=80.0 |
| I.predictive | autonomous predictive CRITICAL is granted only on a full-evidence (status OK) trend, never 3-4 points | ✅ PASS | rul=19.7h status=OK |
| I.predictive | predictive alert report states the projected time-to-critical with CI and the plan-not-react intent | ✅ PASS |  |
| I.predictive | predictive channel de-dups independently (no repeat on unchanged second pass) | ✅ PASS |  |
| I.routing | explicit role-routing matrix covers every autonomous event class (FR7 user-specific notifications) | ✅ PASS | events=['ANOMALY_CRITICAL', 'ANOMALY_WARNING', 'DATA_QUALITY', 'PREDICTIVE_FAILURE', 'PROCUREMENT_RISK', 'RESOLVED', 'WORK_ORDER_RAISED', 'WORK_ORDER_SLA_BREACH'] |
| I.routing | a predicted failure reaches the planner; a procurement risk reaches procurement | ✅ PASS |  |
| J.production | action store is SQLite in WAL mode (transactional, concurrent-safe — not JSON files) | ✅ PASS | journal_mode=wal |
| J.production | DB-backed ledger keeps the v9 escalation contract (CREATED then ESCALATED, never swallowed) | ✅ PASS |  |
| J.production | fail-fast config validation passes on shipped defaults (a broken config refuses to start) | ✅ PASS |  |
| J.production | alert-storm guard and retention are configured with sane bounds | ✅ PASS | storm_cap=25 retention=30d |
| J.production | webhook payloads are HMAC-SHA256 signable (authenticated alert sink, not an injection vector) | ✅ PASS |  |
| J.production | standalone service exposes lifecycle + health (main, --once, /healthz handler) | ✅ PASS |  |

*Suites: A detection accuracy · B prediction sanity · C analytics correctness · D retrieval relevance · E fabrication resistance (constraint C-07) · F Section-5.2 prioritization · G regression & data-robustness · H autonomous-sentinel behavior · I v10 predictive autonomy & role routing · J v11 production readiness. Deterministic — runs without an API key.*