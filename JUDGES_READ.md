# VULCAN — Judge's Read (start here, 5 minutes)

**Tata Steel AI Hackathon 2026 · Round 2 — Agentic AI Challenge**
**Problem: Maintenance Wizard for Industrial Equipment (Steel Manufacturing)**

VULCAN is an LLM **orchestrator agent** for steel-plant maintenance engineers.
It plans which of its **genuine deterministic tools** to call per query — RAG
over manuals/SOPs/history, a layered anomaly engine, four RUL estimators, a
fleet health scanner, delay-log Pareto analytics, a CMMS spares lookup, a
work-order ledger, and a feedback-learning store — and an **autonomous sentinel**
monitors the plant unattended and *predicts* failures before any limit breaks.

Its defining property is **epistemic honesty**: VULCAN never invents a part
number, threshold, stock quantity, or sensor reading. A missing value is named
as an *Information Gap* with an acquisition plan. Every claim carries an
evidence tier and a calibrated confidence band. This is the trait an industrial
operations team can actually deploy.

---

## Verify it yourself in 60 seconds (no API key needed)

```bash
pip install -r requirements.txt
python evals/run_evals.py     # → 51/51 deterministic checks PASS
python -m pytest tests/ -q    # → 43 passed
```

The tool layer (detection, prediction, retrieval, prioritization, honesty,
autonomy) is **measured, not claimed**. The LLM layer needs a key
(`export ANTHROPIC_API_KEY=...; streamlit run app.py`), but every number the
agent reasons over is produced by the tools above, which run and pass offline.

---

## Requirement → Feature → Proof (everything in the PDF is covered)

### Functional Requirements (§6)

| # | PDF requirement | VULCAN feature | Where it lives | Proof |
|---|---|---|---|---|
| 1 | Contextual reasoning via LLM/SLM | Orchestrator agent: plan → parallel tool calls → observe → synthesize | `vulcan/orchestrator.py`, `vulcan/prompts/system_prompt.txt` | `test_streaming_is_genuine...`, agent eval suite |
| 2 | Knowledge integration (manuals, SOPs, history, logs) | Hybrid RAG (BM25 + semantic) with per-chunk provenance & tier | `vulcan/tools/retrieval.py`, `data/knowledge_base/` | eval **D**, `test_hybrid_search_returns_provenance` |
| 3 | Natural-language, multi-turn, context-aware | Streaming chat with history compaction; conversational tool loop | `app.py` (Chat tab), `orchestrator.ask_stream` | `test_history_compaction...` |
| 4 | Explainable, traceable recommendations | Evidence tiers + confidence bands; prioritizer shows per-factor sub-scores, weights, sources | `vulcan/tools/priority.py`, system prompt §0 | eval **F** (arithmetic audit), `test_prioritizer_section_5_2_criteria` |
| 5 | Abnormality detection + failure prediction | 4-layer anomaly engine (threshold/z-score/CUSUM/trend) + 4 RUL models; **predictive** sentinel | `vulcan/tools/anomaly.py`, `vulcan/tools/rul.py`, `sentinel.py` | evals **A, B, I**, `test_predictive_alert_fires_before_any_limit_breach` |
| 6 | Feedback-driven improvement | Persistent feedback store re-weights future priors (min-evidence gated) | `vulcan/learning.py`, `vulcan/tools/cmms.py` | `test_learning_priors_block_aggregates`, `test_learning_prior_requires_minimum_evidence` |
| 7 | Real-time alerting + user-specific notifications | Autonomous alert inbox + role-routing matrix (8 event classes) | `vulcan/autopilot.py`, `vulcan/notify.py` | eval **I.routing**, `test_autopilot_routes_role_notifications` |

### Expected Outputs (§5)

| PDF output | VULCAN delivers it as |
|---|---|
| Probable fault diagnosis + root cause | Agent diagnosis grounded in anomaly layers + retrieved manual/history |
| RUL / remaining lifecycle | Four estimators: linear-drift (80% CI + r²), Weibull conditional, Arrhenius thermal-aging, P-F interval (`rul.py`) |
| Early warning of catastrophic failure | Predictive sentinel: RUL < 72 h → autonomous WARNING, < 24 h → CRITICAL, *before* any limit breach |
| Risk classification + urgency | Anomaly severity bands; work-order priority enum |
| Bottleneck prioritization (§5.2: criticality, delay severity, spares, lead time) | `rank_maintenance_priorities` — transparent weighted fusion of exactly those four factors, each with raw value + source |
| Step-by-step + immediate + long-term plan + spare strategy | Agent synthesis + CMMS spares lookup with true stock/lead time (never optimistic) |
| Structured reports, alert reports, decision summaries, digital logbook | Alerts tab, Work-order ledger, Logbook tab (`data/logbook.md`) |

### Optional Enhancements (§7) — all present

Conversational interface ✓ · Health/trend/anomaly dashboard ✓ · Simulated IoT
feed ✓ · Dynamic per-equipment knowledge base ✓ · Automatic digital logbook ✓ ·
Role-based alerts & recommendations ✓

---

## Why this wins on the criteria Tata Steel published

- **Agentic AI (the theme):** a real planner→act→observe→reflect loop with
  parallel tool execution, not a chatbot wrapper. Autonomy is the *default*,
  and it *predicts*, it doesn't just react.
- **Explainability / feasibility:** an ops team can trust it because it refuses
  to fabricate and shows its evidence for every claim. This is the deployable
  difference, not a feature count.
- **Scalability:** honest scaling path documented (SQLite→Postgres/Timescale,
  CSV→historian/OPC-UA, webhook→Kafka), each behind one code seam
  (`PRODUCTION.md`).
- **Verifiable quality:** 43 tests + 51 eval checks, green on a clean machine.

## Map of the repo

- `vulcan/orchestrator.py` — the agent loop
- `vulcan/tools/` — the genuine tools the agent calls
- `vulcan/autopilot.py`, `sentinel.py`, `vulcan_service.py` — autonomy
- `app.py` — Streamlit UI (Chat · Dashboard · Alerts · Work orders · Live · Logbook)
- `ARCHITECTURE.md` — full design · `PRODUCTION.md` — ops guide
- `DEMO_SCRIPT.md` — the screen-recording shot list
