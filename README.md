# VULCAN , Agentic Maintenance Intelligence Core   




 




<img width="1280" height="525" alt="image" src="https://github.com/user-attachments/assets/0e23c856-fe89-4183-ab90-d03223cc947d" />

**Tata Steel AI Hackathon 2026 · Round 2 — Agentic AI Challenge**
**Theme:** Maintenance Wizard for Industrial Equipment (Steel Manufacturing)

**Live demo:** https://vulcan-maintenance-ai-zzn57vptdg6gd8hbaf6top.streamlit.app

 **Verify offline in 60 seconds (no API key needed):** `pip install -r requirements.txt && python evals/run_evals.py` → **51/51 pass**, `pytest tests/` → **43/43 pass**
****

> **For judges:** `JUDGES_READ.md` maps every requirement to the feature and the test that proves it.

---

## 1. What VULCAN is

Steel plants run on tightly coupled, capital intensive equipment. A single unplanned stop on a critical asset cascades through the whole line into lost production, safety risk and emergency spend. The knowledge needed to prevent it already exists, but it is scattered across equipment manuals, standard operating procedures, sensor logs, failure reports and the memory of whichever expert is on shift.

VULCAN brings that together into one trustworthy decision support agent for maintenance engineers. It is a genuine LLM orchestrator agent, not a chatbot placed over a manual. When an engineer asks a question, or when a sensor reading arrives on its own, VULCAN plans which of its real tools the situation needs, calls those tools, reads the actual results, and then writes an explainable, evidence backed recommendation.

Its defining property is **epistemic honesty**. VULCAN never invents a part number, a threshold, a stock level or a sensor value. When data is missing it names the gap and states how to acquire it, instead of guessing. Every claim it makes carries an evidence tier and a confidence band. In a real plant, fake confidence is exactly what gets a maintenance tool vetoed, so this honesty is not a feature, it is the whole point.

---

## 4. Key features

VULCAN is a real agentic system, not a chatbot. The LLM plans which tools each query needs, calls them, reads the real results, and synthesizes a traceable answer.
<img width="1280" height="565" alt="image" src="https://github.com/user-attachments/assets/aee53707-daef-433b-8dfb-43d309b30f78" />

Four layer anomaly detection combining threshold, z score, CUSUM drift and trend rate catches slow degradation early, before any hard limit is crossed.

Four remaining useful life models, linear regression with an eighty percent confidence interval, Weibull conditional life, Arrhenius thermal aging and the P to F interval, forecast failure ahead of time.
<img width="1280" height="564" alt="image" src="https://github.com/user-attachments/assets/38ddf23a-e191-4d8b-8297-ea1d6799819e" />

Hybrid knowledge retrieval over manuals, SOPs and failure history, fusing TF-IDF and BM25, with chunk level source citations on every claim.
<img width="1280" height="569" alt="image" src="https://github.com/user-attachments/assets/0a6c4718-9061-4f87-9e22-79688d1104e6" />

Constraint aware prioritization that fuses process criticality, delay severity, spares availability and procurement lead time into one transparent risk score, flagging when remaining life is shorter than the part lead time.
<img width="1280" height="443" alt="image" src="https://github.com/user-attachments/assets/4bc5900d-161d-4ccd-a7fd-b4badae43822" />

Epistemic honesty as the core principle. VULCAN never invents a part number, threshold, stock level or reading. Missing data is named as an information gap with a plan to acquire it.
<img width="1280" height="626" alt="image" src="https://github.com/user-attachments/assets/aedb7c71-a22e-4247-ae91-1faefb19aeef" />

Autonomous sentinel that monitors the whole plant unattended, predicting failures before any limit breaks, routing role specific alerts, and raising spares checked work orders on its own.

Production minded engineering: SQLite write ahead log ledger, HMAC signed webhooks, a health endpoint, structured logging with key redaction, Docker packaging and continuous <img width="1280" height="539" alt="image" src="https://github.com/user-attachments/assets/3eb3dfd1-2f18-4919-abc9-9d5a0924e888" />
integration.

Measured quality: 51 of 51 evaluation checks and 43 unit tests, all runnable offline in about 60 seconds.

---

## 2. Working principle

VULCAN reasons in one traceable pass, the way a careful engineer would.

It first **detects**, running a four layer anomaly engine that combines a threshold check, a statistical z score, a CUSUM drift test and a trend rate, so slow degradation is caught long before a hard limit is crossed. It then **predicts**, estimating remaining useful life with a linear regression model that reports an eighty percent confidence interval and a fit quality score, backed by three more estimators for Weibull conditional life, Arrhenius thermal aging and the P to F interval. It then **diagnoses**, retrieving the relevant manual, SOP and past failure history and reasoning to the most probable root cause while ruling out alternatives. It then **prioritizes**, fusing process criticality, delay severity, spares availability and procurement lead time into one transparent risk score where every sub factor shows its raw value and source. Finally it **recommends** a concrete plan and flags the dangerous case where remaining life is shorter than the part lead time.
<img width="3111" height="2000" alt="image" src="https://github.com/user-attachments/assets/f18a1d02-7bce-4248-8427-e386e3398ff5" />

Crucially, VULCAN does not wait to be asked. An autonomous sentinel runs continuously in the background, monitoring the whole plant with every browser tab closed. It detects problems on its own, predicts failures from the remaining useful life trend before any limit is breached, routes role specific notifications to the right people, and raises spares checked work orders automatically on critical conditions. This turns maintenance from firefighting after a breakdown into planning before it.

The chat agent and the autonomous sentinel share the exact same tested tool layer, so what the agent says and what the sentinel does can never quietly diverge.

---

## 3. Architecture

The intelligence is split deliberately into two layers, each doing what it is best at.

<img width="1235" height="797" alt="image" src="https://github.com/user-attachments/assets/2cd8e6d1-76c6-4358-b9e5-a03f0509993a" />


The **behavioral layer** is an 812 line system prompt that defines twelve reasoning agents (parse and plan through to logbook), a response depth ladder so the output matches the input, evidence tiers with calibrated confidence, role aware rendering for engineers, supervisors, procurement and safety, and twenty three inviolable constraints, chief among them: never fabricate a value.

The **computational layer** is a set of deterministic Python tools the agent genuinely invokes: hybrid retrieval (TF-IDF plus BM25) over manuals, SOPs and history with chunk level provenance; a four layer anomaly engine; four remaining useful life estimators; a fleet wide health scanner; delay log Pareto analytics; a constraint aware prioritizer; a CMMS spares lookup; a work order ledger; and a feedback learning store.

The **orchestrator** runs the agentic cycle: the LLM reads the query, plans which tools the input needs, the host executes those calls and returns real results, and the loop repeats until the model produces its final answer.

Full design detail is in `ARCHITECTURE.md`.

---

## 4. Technology stack

Python 3.10+; Anthropic Messages API with native tool use (model configurable, default `claude-sonnet-4-6`); Streamlit chat UI plus a CLI; scikit-learn TF-IDF and BM25 for retrieval; pandas, numpy and scipy for analytics and statistics; SQLite with write ahead logging for persistence. Everything besides the LLM API runs locally with no GPU, so the prototype is reproducible on any laptop.

---

## 5. How to run it

```bash
# 1. Install (Python 3.10 or newer)
pip install -r requirements.txt

# 2. Configure your API key — choose ONE of:
#    a) create a .env file in the project root containing:
#         ANTHROPIC_API_KEY=sk-ant-your-key-here
#    b) export it in your shell:
export ANTHROPIC_API_KEY=sk-ant-your-key-here
#    c) or paste it into the app sidebar at runtime

# 3. Run the app (this single command also starts the autonomous sentinel)
streamlit run app.py

#    ...or the terminal chat version:
python cli.py

#    ...or the headless autonomous monitor:
python sentinel.py --watch 300        # scan every 5 minutes
```

To verify the engine without any API key:

```bash
python evals/run_evals.py     # 51 deterministic checks
pytest tests/                 # 43 unit tests
```

The app has six tabs: **Chat** (the agent, with a live tool trace), **Dashboard** (fleet health scores and trend charts), **Alerts** (autonomously generated reactive and predictive alerts), **Work Orders** (the auto raised and agent raised action ledger), **Live Monitor** (a simulated sensor feed you can step through), and **Logbook** (a persistent maintenance log). A status strip at the top shows the autonomous sentinel running.
<img width="1280" height="151" alt="image" src="https://github.com/user-attachments/assets/cb3027e2-6990-4e89-b8e2-313582648b26" />

---




## 6. Demo data

All bundled data is synthetic and labeled as such. The seeded scenario is a caster mold oscillator, **CC2-MO-01**, with accelerating vibration (2.8 rising to about 7.4 mm/s over twelve days) against OEM limits of 7.0 warning and 11.0 trip, a maintenance history containing an analogous confirmed bearing failure, one bearing in stock, and a delay log where **HSM-COILER-01** is the chronic plant bottleneck.

To use real plant data, drop real files into the same slots under `data/` and restart. No code changes are needed.

<img width="1280" height="565" alt="image" src="https://github.com/user-attachments/assets/5f36c844-5708-4a7c-a120-c728423c0be1" />


---

## 7. Measured quality

The tool layer passes **51 of 51** deterministic evaluation checks (`python evals/run_evals.py`) covering detection accuracy, false positive control, RUL sanity, delay analytics, retrieval relevance, fabrication resistance probes, and prioritization audits. A separate **43** unit tests (`pytest tests/`) plus a GitHub Actions workflow keep everything continuously verified. Both run offline in about sixty seconds with no API key. The agent reasons over numbers that this tested code produces, so what it says and what it does can never diverge.

---

## 8. Future work

The prototype is built so that scaling is an engineering step, not a rewrite. Every component has a documented drop in successor behind a single clean code seam:

The SQLite store moves to PostgreSQL with TimescaleDB for plant wide sensor history. The CSV sensor files are replaced by a live plant historian over OPC-UA or MQTT. The webhook alerts move onto a Kafka message bus for plant scale delivery. Keyword retrieval upgrades to pgvector semantic search. The single degradation trend grows into per failure mode survival models with multivariate anomaly fusion across correlated sensors.

Beyond scaling, the next steps are a domain tuned small language model fine tuned on plant maintenance data (the dataset generator is already included under `finetune/`), deeper integration with the existing CMMS and ERP systems with role based single sign on and mobile alerts, and a stronger feedback loop so every confirmed or corrected diagnosis makes the next prediction better and lets VULCAN adapt to each individual machine over time.

---

## 9. Notes and limitations

VULCAN is decision support: a human engineer authorizes every action. It requires internet access to the Anthropic API; the model name is configurable. The four numeric RUL models are implemented as genuine tools; other reliability branches are applied as transparent reasoning when their inputs are supplied. All bundled data is synthetic.
