"""VULCAN — Streamlit app (v8: chat + dashboard + AUTOPILOT + alerts +
work orders + live monitor + logbook).

Run:  streamlit run app.py
Needs ANTHROPIC_API_KEY in the environment (or pasted + validated in the
sidebar).

v8 headline: the autonomous loop now runs INSIDE the app. Switch the
Autopilot on and VULCAN scans the plant every few seconds with zero human
action — detecting, alerting, logging, and auto-raising work orders on
CRITICAL conditions. The Chat agent and the Autopilot share the same
tested tool layer, so what the agent says and what the autopilot does can
never diverge.
"""
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vulcan import datastore  # noqa: E402
from vulcan.autopilot import autopilot_tick  # noqa: E402
from vulcan.config import DATA_DIR, KNOWLEDGE_BASE_DIR, SENSOR_DATA_DIR  # noqa: E402
from vulcan.tools.anomaly import detect_anomalies, scan_plant_health  # noqa: E402
from vulcan.tools.delay_analytics import analyze_delay_log  # noqa: E402
from vulcan.tools.live import (append_logbook, read_logbook,  # noqa: E402
                               reset_live_stream, simulate_next_reading)
from vulcan.tools.retrieval import get_kb  # noqa: E402
from vulcan.tools.workorders import list_work_orders, update_work_order  # noqa: E402
from vulcan.daemon import ensure_autostarted  # noqa: E402
from vulcan.notify import ROLES, read_notifications  # noqa: E402

ALERTS_DIR = DATA_DIR / "alerts"

# v10 — autonomy is the DEFAULT: the monitoring daemon starts with the
# server (set VULCAN_DAEMON_AUTOSTART=0 to opt out). Idempotent per rerun.
_daemon = ensure_autostarted()

st.set_page_config(page_title="VULCAN — Maintenance Wizard",
                   page_icon="🔥", layout="wide")

# ─────────────────── industrial steel-plant theme (cosmetic only) ───────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@500;600;700&family=JetBrains+Mono&display=swap');
.stApp {
    background:
      radial-gradient(circle at 18% -5%, rgba(242,103,34,0.10), transparent 45%),
      radial-gradient(circle at 100% 100%, rgba(70,90,120,0.08), transparent 40%),
      linear-gradient(160deg, #0B0E13 0%, #11161F 55%, #080A0E 100%);
}
h1, h2, h3 {
    font-family: 'Rajdhani', sans-serif !important;
    color: #F26722 !important;
    letter-spacing: .5px;
    text-transform: uppercase;
}
section[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #161B22, #0B0E13);
    border-right: 1px solid rgba(242,103,34,0.30);
}
[data-testid="stMarkdownContainer"] h2:first-of-type {
    border-left: 5px solid #F26722;
    padding-left: 14px;
    text-shadow: 0 0 22px rgba(242,103,34,0.55);
}
[data-testid="stChatMessage"] {
    background: linear-gradient(145deg, #1A1F29, #11151C);
    border: 1px solid rgba(255,255,255,0.06);
    border-radius: 12px;
    box-shadow: inset 0 1px 0 rgba(255,255,255,0.05), 0 6px 18px rgba(0,0,0,0.55);
}
.stButton button {
    background: linear-gradient(180deg, #F26722, #C2491A);
    color: #fff; border: none; font-weight: 600; border-radius: 8px;
    box-shadow: 0 0 14px rgba(242,103,34,0.40); transition: .2s;
}
.stButton button:hover {
    box-shadow: 0 0 26px rgba(242,103,34,0.75);
    transform: translateY(-1px);
}
.stTabs [aria-selected="true"] { color: #F26722 !important; }
.stTabs [data-baseweb="tab-highlight"] { background: #F26722 !important; }
[data-testid="stMetric"] {
    background: linear-gradient(145deg, #161B22, #0E1218);
    border: 1px solid rgba(242,103,34,0.20);
    border-radius: 10px; padding: 14px;
}
[data-testid="stMetricValue"] {
    color: #F26722 !important;
    font-family: 'Rajdhani', sans-serif !important;
}
hr { border-color: rgba(242,103,34,0.25) !important; }
code { font-family: 'JetBrains Mono', monospace !important; }
.stDataFrame { border: 1px solid rgba(242,103,34,0.15); border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

st.markdown(
    "## 🔥 VULCAN — Agentic Maintenance Intelligence Core\n"
    "*Tata Steel AI Hackathon 2026 · Round 2 · decision-support for "
    "maintenance engineers*"
)

# ─────────────────────────── sidebar ───────────────────────────
with st.sidebar:
    st.header("⚙️ Configuration")
    key_in = st.text_input("ANTHROPIC_API_KEY", type="password",
                           value=os.environ.get("ANTHROPIC_API_KEY", ""))
    if key_in:
        os.environ["ANTHROPIC_API_KEY"] = key_in.strip().strip('"').strip("'")
    model = st.text_input("Model",
                          value=os.environ.get("ANTHROPIC_MODEL",
                                               "claude-sonnet-4-6"))
    os.environ["ANTHROPIC_MODEL"] = model

    if st.button("🔑 Validate key", use_container_width=True):
        try:
            import anthropic
            anthropic.Anthropic().models.list(limit=1)
            st.success("Key accepted by the Anthropic API ✅")
        except anthropic.AuthenticationError:
            st.error("401 — this key is INVALID. Generate a fresh one at "
                     "console.anthropic.com → API keys, copy it whole "
                     "(starts with `sk-ant-`), and paste it with no spaces "
                     "or quotes.")
        except Exception as exc:
            st.error(f"Could not reach the API: {exc}")

    st.divider()
    st.header("🤖 Autopilot (autonomous mode)")
    st.caption("Hands-free loop: scan → detect → alert → logbook → "
               "auto work-order on CRITICAL. Runs in-app every cycle, "
               "no human action needed.")
    st.session_state["autopilot_on"] = st.toggle(
        "Autopilot ON", value=st.session_state.get("autopilot_on", False))
    st.session_state["auto_stream"] = st.toggle(
        "Also auto-advance the simulated IoT feed (demo)",
        value=st.session_state.get("auto_stream", False),
        help="Streams one clearly-SYNTHETIC reading per cycle for "
             "CC2-MO-01 vibration so judges can watch a degradation be "
             "caught live. Writes only to the reversible overlay file.")
    interval = st.slider("Cycle interval (seconds)", 5, 60, 15)

    st.subheader("🛰 Server-side daemon (autostarts in v10)")
    st.caption("True unattended autonomy: a background thread in the "
               "server process keeps monitoring — predictive RUL alerts, "
               "role-routed notifications, spares-checked work orders, "
               "SLA watchdog — with EVERY browser tab closed. v10: it "
               "starts WITH the server (VULCAN_DAEMON_AUTOSTART=0 to "
               "opt out); the buttons below are a manual override.")
    from vulcan.daemon import get_daemon
    daemon = get_daemon()
    d1, d2 = st.columns(2)
    if d1.button("▶ Start daemon", use_container_width=True,
                 disabled=daemon.running):
        daemon.start(interval=interval)
        st.rerun()
    if d2.button("⏹ Stop daemon", use_container_width=True,
                 disabled=not daemon.running):
        daemon.stop()
        st.rerun()
    st.caption(f"Status: {'🟢 RUNNING' if daemon.running else '⚪ stopped'}"
               + ((" (autostarted)" if daemon.autostarted else "")
                  + f" · since {daemon.started_at} · "
                  f"{daemon.cycles} unattended cycle(s)"
                  if daemon.running else ""))

    st.divider()
    st.header("🧪 Demo scenarios")
    SCENARIOS = {
        "Sensor anomaly + RUL (CC2 oscillator)":
            "Engineer here. Mold oscillator CC2-MO-01 on Caster 2 is showing "
            "rising vibration over the last days. Check the sensor history "
            "for vibration_mm_s, tell me what's wrong, the RUL, the risk, "
            "and what to do. We have a 6 h maintenance window on Saturday.",
        "Plant triage from delay log":
            "Analyze our delay log and tell me what to fix first across the "
            "plant — give me the triage board and name the bottleneck.",
        "Fleet health check (proactive)":
            "Scan the whole plant — which assets should I be worried about "
            "right now, and why?",
        "Weibull RUL with OEM data":
            "OEM data for the coiler mandrel drive bearing class: Weibull "
            "beta 2.4, eta 18000 h. This unit has run 12000 h. What's the "
            "remaining life and should we plan replacement at the July "
            "shutdown (about 700 h away)?",
        "Spares + procurement check":
            "Do we have a spare oscillator drive bearing in stock for "
            "CC2-MO-01, and is the lead time a problem given its condition?",
        "Raise a work order from diagnosis":
            "Diagnose CC2-MO-01 vibration, and if the evidence supports it, "
            "raise a HIGH priority work order for the Saturday window with "
            "the evidence reference attached. Then show me all open work "
            "orders.",
        "Hinglish supervisor query":
            "Supervisor bol raha hoon — CC2 ka oscillator kab tak chalega? "
            "Production rokna padega kya?",
        "Feedback (learning loop)":
            "Feedback on the CC2-MO-01 report: CONFIRMED — teardown found "
            "outer-race spalling on the drive-side bearing, exactly as "
            "diagnosed. Record it.",
    }
    for label, text in SCENARIOS.items():
        if st.button(label, use_container_width=True):
            st.session_state["queued_input"] = text

    st.divider()
    st.header("📚 Dynamic knowledge base")
    up = st.file_uploader("Add manual / SOP / history (.md, .txt, .pdf)",
                          type=["md", "txt", "pdf"],
                          accept_multiple_files=True)
    if up and st.button("Ingest into knowledge base"):
        from vulcan.tools.retrieval import ingest_pdf
        ok = 0
        for f in up:
            if f.name.lower().endswith(".pdf"):
                res = ingest_pdf(f.getvalue(), f.name)
                if res["status"] == "OK":
                    ok += 1
                else:
                    st.warning(f"{f.name}: {res['message']}")
            else:
                (KNOWLEDGE_BASE_DIR / f.name).write_bytes(f.getvalue())
                ok += 1
        get_kb().reload()
        st.success(f"Ingested {ok} file(s) — index rebuilt. VULCAN can "
                   "now cite them.")

    st.divider()
    st.header("📈 Sensor data ingestion")
    sup = st.file_uploader(
        "Add sensor readings CSV (timestamp,equipment_id,parameter,"
        "value,unit)", type=["csv"], key="sensor_up")
    if sup and st.button("Ingest sensor CSV"):
        import io
        try:
            new_df = pd.read_csv(io.BytesIO(sup.getvalue()))
            needed = {"timestamp", "equipment_id", "parameter", "value"}
            if not needed.issubset(new_df.columns):
                st.error(f"CSV must contain columns: {sorted(needed)}")
            else:
                bad = pd.to_datetime(new_df["timestamp"],
                                     errors="coerce").isna().sum()
                dest = SENSOR_DATA_DIR / sup.name
                new_df.to_csv(dest, index=False)
                datastore.invalidate()
                msg = f"Stored {len(new_df)} readings as {dest.name}."
                if bad:
                    msg += (f" ⚠️ {bad} row(s) have unparseable timestamps "
                            "and will be skipped by the analyzers "
                            "(malformed-input rule).")
                st.success(msg)
        except Exception as exc:
            st.error(f"Could not parse CSV: {exc}")

    st.divider()
    if st.button("🔄 New session", use_container_width=True):
        st.session_state.pop("orchestrator", None)
        st.session_state.pop("chat", None)
        st.rerun()

if "chat" not in st.session_state:
    st.session_state["chat"] = []
if "autopilot_log" not in st.session_state:
    st.session_state["autopilot_log"] = []


def get_orchestrator():
    if "orchestrator" not in st.session_state:
        from vulcan.orchestrator import VulcanOrchestrator
        st.session_state["orchestrator"] = VulcanOrchestrator()
    return st.session_state["orchestrator"]


# v9 efficiency: every Streamlit rerun (each chat token batch, button click,
# tab switch) re-executes this whole script — v8 therefore re-ran the full
# fleet scan + delay Pareto on EVERY rerun. These wrappers key the result on
# the data fingerprint, so the computation repeats only when the data does.
@st.cache_data(show_spinner=False)
def cached_scan(fp: tuple):
    return scan_plant_health()


@st.cache_data(show_spinner=False)
def cached_delay_analysis(fp: tuple, mtime: float):
    return analyze_delay_log()


# ──────────────────── AUTOPILOT status strip (autonomous) ────────────────────
@st.fragment(run_every=f"{interval}s")
def autopilot_strip():
    from vulcan.daemon import get_daemon
    daemon = get_daemon()
    # v10 fix: when the server daemon is running, the per-tab fragment is
    # ALWAYS a viewer, never a second driver — v9 let both drive at once
    # if the Autopilot toggle was also on (interleaved logs, demo stream
    # advanced at double cadence).
    if daemon.running:
        last = daemon.history[0] if daemon.history else {}
        st.success(f"🛰 Server daemon RUNNING"
                   f"{' (autostarted — zero-touch autonomy)' if daemon.autostarted else ''}"
                   f" — {daemon.cycles} "
                   f"unattended cycle(s) since {daemon.started_at}; "
                   f"last: {last.get('at', '—')} · "
                   f"{len(last.get('alerts', []))} alert(s). "
                   "Monitoring continues even with every tab closed.")
        with st.expander("Daemon activity (server-side, tab-independent)"):
            for s in daemon.history[:15]:
                st.code(f"{s.get('at')} | "
                        f"alerts={[a['key'] for a in s.get('alerts', [])]}"
                        f" | WOs={s.get('work_orders_raised', [])}"
                        f" | notif={s.get('notifications', [])}"
                        f" | SLA={s.get('sla_breaches', [])}"
                        f" | resolved={s.get('resolved', [])}"
                        + (f" | ERROR={s['error']}" if "error" in s
                           else ""), language="text")
        return
    if not st.session_state.get("autopilot_on"):
        st.caption("🤖 Autopilot OFF and daemon stopped — flip the sidebar "
                   "toggle for the in-tab loop, or restart the 🛰 server "
                   "daemon for autonomy that survives closed tabs. "
                   "(Headless twin: `python sentinel.py --watch 300`.)")
        return
    summary = autopilot_tick(
        auto_stream=st.session_state.get("auto_stream", False))
    if summary.get("skipped"):
        st.caption(f"🤖 cycle @ {summary['at']} skipped — "
                   f"{summary['skipped']} (single-flight guard).")
        return
    st.session_state["autopilot_log"] = (
        [summary] + st.session_state["autopilot_log"])[:30]
    n_alerts = len(summary["alerts"])
    n_wo = len(summary["work_orders_raised"])
    bits = [f"🤖 **Autopilot cycle @ {summary['at']}**",
            f"streamed {len(summary['streamed'])} synthetic reading(s)"
            if summary["streamed"] else "no new readings",
            f"🚨 {n_alerts} new alert(s)" if n_alerts else "no new alerts",
            f"🛠 auto-raised {n_wo} work order(s)" if n_wo else None,
            f"✅ {len(summary['resolved'])} resolved"
            if summary["resolved"] else None]
    line = " · ".join(b for b in bits if b)
    if n_alerts:
        st.error(line)
    else:
        st.success(line)
    with st.expander("Autopilot activity log (this session)"):
        for s in st.session_state["autopilot_log"]:
            st.code(f"{s['at']} | alerts={[a['key'] for a in s['alerts']]} "
                    f"| WOs={s['work_orders_raised']} "
                    f"| resolved={s['resolved']}", language="text")


autopilot_strip()

tab_chat, tab_dash, tab_alerts, tab_wo, tab_live, tab_log = st.tabs(
    ["💬 Chat", "📊 Dashboard", "🚨 Alerts", "🛠 Work Orders",
     "📡 Live Monitor", "📒 Logbook"])

# ─────────────────────────── CHAT ───────────────────────────
with tab_chat:
    for role, text in st.session_state["chat"]:
        with st.chat_message(role):
            st.markdown(text)

    user_input = st.chat_input("Describe the equipment issue, paste a "
                               "sensor dump, or ask anything…")
    if not user_input and "queued_input" in st.session_state:
        user_input = st.session_state.pop("queued_input")

    if user_input:
        st.session_state["chat"].append(("user", user_input))
        with st.chat_message("user"):
            st.markdown(user_input)
        with st.chat_message("assistant"):
            try:
                orch = get_orchestrator()
                n_before = len(orch.tool_trace)
                # v8: streamed rendering — tokens appear as they arrive
                answer = st.write_stream(orch.ask_stream(user_input))
                new_calls = orch.tool_trace[n_before:]
                if new_calls:
                    with st.expander(
                            f"🔧 Tool trace — {len(new_calls)} genuine tool "
                            f"call(s) this turn (Section 3B transparency)"):
                        for c in new_calls:
                            st.code(f"{c['tool']}({c['input']}) → "
                                    f"{c['output_status']} [{c['ms']} ms]")
                st.session_state["chat"].append(("assistant", answer))
            except Exception as exc:
                err = f"⚠️ {exc}"
                st.error(err)
                st.session_state["chat"].append(("assistant", err))

    if st.session_state["chat"]:
        c1, c2 = st.columns(2)
        with c1:
            if st.button("📒 Save last VULCAN reply to logbook"):
                last = next((t for r, t in reversed(st.session_state["chat"])
                             if r == "assistant"), None)
                if last:
                    append_logbook(last)
                    st.success("Saved to data/logbook.md")
        with c2:
            transcript = "\n\n".join(
                f"**{r.upper()}**\n\n{t}"
                for r, t in st.session_state["chat"])
            st.download_button("⬇️ Export chat transcript (.md)",
                               transcript, file_name="vulcan_session.md")

# ─────────────────────────── DASHBOARD ───────────────────────────
with tab_dash:
    st.subheader("Plant health overview")
    scan = cached_scan(datastore.fingerprint())
    if scan["n_assets_scanned"]:
        dfh = pd.DataFrame(scan["assets"])
        worst = dfh.iloc[0]
        cols = st.columns(3)
        cols[0].metric("Assets monitored", scan["n_assets_scanned"])
        cols[1].metric("Worst health score",
                       f"{worst['health_score']}/100",
                       delta=worst["status"], delta_color="inverse")
        cols[2].metric("Worst asset", f"{worst['equipment_id']}")
        st.dataframe(
            dfh[["equipment_id", "parameter", "latest_value", "unit",
                 "health_score", "status", "trend", "last_timestamp"]],
            use_container_width=True, hide_index=True)
        st.caption(scan["scoring_note"])
    else:
        st.info("No sensor data found in data/sensor_data/.")

    st.subheader("Parameter trend")
    readings = datastore.get_all_readings()
    if not readings.empty:
        pair = st.selectbox(
            "Equipment / parameter", datastore.list_pairs(),
            format_func=lambda p: f"{p[0]} — {p[1]}")
        sel = readings[(readings["equipment_id"] == pair[0])
                       & (readings["parameter"] == pair[1])] \
            .set_index("timestamp")[["value"]].sort_index()
        th = datastore.get_thresholds()
        if th is not None:
            row = th[(th["equipment_id"] == pair[0])
                     & (th["parameter"] == pair[1])]
            if len(row):
                sel["warning_limit"] = float(row["warning"].iloc[0])
                sel["critical_limit"] = float(row["critical"].iloc[0])
        st.line_chart(sel)

    st.subheader("Delay Pareto (lost minutes by cause)")
    _dl = DATA_DIR / "delay_log.csv"
    d = cached_delay_analysis(datastore.fingerprint(),
                              _dl.stat().st_mtime if _dl.exists() else 0.0)
    if d.get("status") == "OK":
        pareto = pd.DataFrame(d["pareto_by_cause"]).set_index("cause")
        st.bar_chart(pareto["lost_minutes"])
        st.caption(f"Bottleneck candidate: "
                   f"**{d['bottleneck_candidate']['equipment_id']}** — "
                   f"{d['bottleneck_candidate']['share_pct']}% of all lost "
                   f"time. Chronic repeat offenders: "
                   + ", ".join(f"{c['equipment_id']} ({c['cause']}, "
                               f"×{c['recurrences']})"
                               for c in d["chronic_repeat_offenders"]))

# ─────────────────────────── ALERTS INBOX ───────────────────────────
with tab_alerts:
    st.subheader("🚨 Abnormal & predictive alert inbox "
                 "(autonomously generated)")
    st.caption("Every report below was written WITHOUT a human query — by "
               "the server daemon, the in-app Autopilot, or the headless "
               "sentinel. v10 adds PREDICT_* reports: failures forecast "
               "from the RUL trend BEFORE any limit is breached. "
               "State-aware de-dup: an unchanged condition alerts once; "
               "escalations re-alert; recoveries log as RESOLVED.")
    files = sorted(list(ALERTS_DIR.glob("ALERT_*.md"))
                   + list(ALERTS_DIR.glob("PREDICT_*.md")),
                   key=lambda p: p.name.split("_", 1)[1], reverse=True) \
        if ALERTS_DIR.exists() else []
    if not files:
        st.info("No alerts yet. The autostarted daemon will catch the "
                "seeded CC2-MO-01 degradation within one cycle.")
    for f in files[:25]:
        body = f.read_text(encoding="utf-8")
        sev = "CRITICAL" if "CRITICAL" in body[:130] else "WARNING"
        icon = ("🔮" if f.name.startswith("PREDICT") else
                ("🚨" if sev == "CRITICAL" else "⚠️"))
        with st.expander(f"{icon} {f.name}"):
            st.markdown(body)
            if st.button("🧠 Ask VULCAN about this alert",
                         key=f"ask_{f.name}"):
                st.session_state["queued_input"] = (
                    f"Autonomous alert {f.name} fired. Give me the full "
                    f"diagnostic: root cause, RUL, risk classification and "
                    f"prioritized actions, with citations.")
                st.info("Loaded into the Chat tab — switch tabs and send.")

    st.divider()
    st.subheader("🔔 Role-routed notifications (FR7)")
    st.caption("Every autonomous decision is routed to the roles who must "
               "act on it via an explicit matrix (vulcan/notify.py) and "
               "persisted to the SQLite store (data/vulcan.db). Set "
               "VULCAN_WEBHOOK_URL to fan these out to Slack/Teams/SMS.")
    role = st.selectbox("View as role", ("all",) + ROLES)
    notifs = read_notifications(role="" if role == "all" else role,
                                limit=30)
    if not notifs:
        st.info("No notifications yet for this role.")
    for n in notifs:
        badge = "🚨" if n["severity"] == "CRITICAL" else "🔔"
        st.markdown(f"{badge} **{n['title']}**  \n"
                    f"`{n['at']}` · `{n['event_type']}` → "
                    f"{', '.join(n['roles'])}"
                    + (f" · ref `{n['ref']}`" if n.get("ref") else "")
                    + (f"  \n{n['body']}" if n.get("body") else ""))

# ─────────────────────────── WORK ORDERS ───────────────────────────
with tab_wo:
    st.subheader("🛠 Work-order ledger")
    st.caption("Raised automatically by the Autopilot on CRITICAL alerts, "
               "or by the agent/engineer from a diagnosis. De-duplicated "
               "while an order is open; every order carries its evidence "
               "reference.")
    wos = list_work_orders()["work_orders"]
    if not wos:
        st.info("No work orders yet. They appear here when the Autopilot "
                "sees a CRITICAL condition, or when you ask the agent to "
                "raise one.")
    else:
        st.dataframe(pd.DataFrame(wos)[
            ["id", "created_at", "equipment_id", "parameter", "title",
             "priority", "status", "source", "evidence_ref"]],
            use_container_width=True, hide_index=True)
        open_ids = [w["id"] for w in wos
                    if w["status"] in ("OPEN", "IN_PROGRESS")]
        if open_ids:
            c1, c2, c3 = st.columns([2, 2, 1])
            sel_id = c1.selectbox("Work order", open_ids)
            new_status = c2.selectbox("New status",
                                      ["IN_PROGRESS", "DONE", "CANCELLED"])
            if c3.button("Update"):
                update_work_order(sel_id, new_status)
                st.rerun()

# ─────────────────────────── LIVE MONITOR ───────────────────────────
with tab_live:
    st.subheader("📡 Simulated IoT feed → real-time alerting")
    st.caption("Manual mode of the same feed the Autopilot drives. Streams "
               "the next plausible (clearly SYNTHETIC) reading by "
               "continuing the asset's trend, then runs the layered anomaly "
               "engine on it.")
    eq = st.selectbox("Asset", ["CC2-MO-01", "HSM-COILER-01"])
    param = st.selectbox("Parameter",
                         ["vibration_mm_s", "bearing_temp_C"]
                         if eq == "CC2-MO-01" else ["vibration_mm_s"])
    lc1, lc2 = st.columns(2)
    with lc1:
        if st.button("▶️ Stream next reading (+8 h)"):
            new = simulate_next_reading(eq, param)
            if new["status"] != "OK":
                st.error("Not enough history for this pair.")
            else:
                st.session_state["last_live"] = new
    with lc2:
        if st.button("🧹 Reset simulated readings (restore demo baseline)"):
            st.session_state.pop("last_live", None)
            st.success("Synthetic overlay deleted — pristine demo data "
                       "restored." if reset_live_stream()
                       else "No synthetic readings to remove — baseline "
                            "already pristine.")
    if "last_live" in st.session_state:
        new = st.session_state["last_live"]
        st.info(f"[SYNTHETIC] {new['timestamp']} · {new['equipment_id']} · "
                f"{new['parameter']} = **{new['value']} {new['unit']}**")
        chk = detect_anomalies(new["equipment_id"], new["parameter"])
        fired = chk.get("layers_fired", [])
        crit = [f for f in fired if f["severity"] == "CRITICAL"]
        warn = [f for f in fired if f["severity"] == "WARNING"]
        if crit:
            st.error("🚨 CRITICAL — " + "; ".join(f["detail"] for f in crit))
        elif warn:
            st.warning("⚠️ WARNING — " + "; ".join(f["detail"]
                                                   for f in warn))
        else:
            st.success("✅ No anomaly layer fired.")
        if fired and st.button("🧠 Ask VULCAN about this alert"):
            st.session_state["queued_input"] = (
                f"ALARM: {new['equipment_id']} {new['parameter']} just read "
                f"{new['value']} {new['unit']} at {new['timestamp']}. "
                f"Anomaly layers fired: {[f['layer'] for f in fired]}. "
                f"Full diagnostic please.")
            st.info("Loaded into the Chat tab — switch tabs and send.")

# ─────────────────────────── LOGBOOK ───────────────────────────
with tab_log:
    st.subheader("📒 Digital maintenance logbook (persistent)")
    content = read_logbook()
    st.markdown(content)
    st.download_button("⬇️ Download logbook (.md)", content,
                       file_name="vulcan_logbook.md")
