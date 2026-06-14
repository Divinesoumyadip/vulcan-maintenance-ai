"""VULCAN orchestrator — the agentic loop.

The LLM (with the VULCAN v7.0 system prompt) plans which tools to call;
this module executes them and feeds genuine results back, satisfying
Section 3B (Tool-Augmented Mode): no phantom tools, full provenance.

Requires ANTHROPIC_API_KEY in the environment.
Model configurable via ANTHROPIC_MODEL (see vulcan/config.py).
"""
from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

import anthropic

from vulcan.config import (ANTHROPIC_MODEL, MAX_TOKENS, MAX_TOOL_ROUNDS,
                           load_system_prompt)
from vulcan.learning import learning_priors_block
from vulcan.tools.anomaly import detect_anomalies, scan_plant_health
from vulcan.tools.cmms import (get_feedback_history, query_spares,
                               record_feedback)
from vulcan.tools.delay_analytics import analyze_delay_log
from vulcan.tools.priority import rank_maintenance_priorities
from vulcan.tools.retrieval import search_knowledge_base
from vulcan.tools.rul import (estimate_rul, estimate_rul_arrhenius,
                              estimate_rul_pf_interval, estimate_rul_weibull)
from vulcan.tools.workorders import (create_work_order, list_work_orders,
                                     update_work_order)

# ─────────────────── tool registry (schema + python impl) ───────────────────

TOOL_IMPLS: dict[str, Callable[..., dict]] = {
    "search_knowledge_base": search_knowledge_base,
    "detect_anomalies": detect_anomalies,
    "scan_plant_health": scan_plant_health,
    "estimate_rul": estimate_rul,
    "estimate_rul_weibull": estimate_rul_weibull,
    "estimate_rul_arrhenius": estimate_rul_arrhenius,
    "estimate_rul_pf_interval": estimate_rul_pf_interval,
    "analyze_delay_log": analyze_delay_log,
    "rank_maintenance_priorities": rank_maintenance_priorities,
    "query_spares": query_spares,
    "record_feedback": record_feedback,
    "get_feedback_history": get_feedback_history,
    "create_work_order": create_work_order,
    "list_work_orders": list_work_orders,
    "update_work_order": update_work_order,
}

_EXTRA_SCHEMAS = [
    {
        "name": "create_work_order",
        "description": "Raise a tracked maintenance work order in the "
                       "ledger (SQLite, data/vulcan.db). Use when a "
                       "diagnosis warrants ACTION — convert the "
                       "recommendation into a tracked task with priority. "
                       "Idempotent: an OPEN order on the same "
                       "asset/parameter is returned, never duplicated.",
        "input_schema": {
            "type": "object",
            "properties": {
                "equipment_id": {"type": "string"},
                "title": {"type": "string",
                          "description": "short imperative task title"},
                "priority": {"type": "string",
                             "enum": ["LOW", "MEDIUM", "HIGH", "CRITICAL"]},
                "parameter": {"type": "string",
                              "description": "triggering parameter, if any"},
                "details": {"type": "string",
                            "description": "evidence-backed task detail"},
                "evidence_ref": {"type": "string",
                                 "description": "report id / alert file the "
                                                "order traces back to"},
            },
            "required": ["equipment_id", "title"],
        },
    },
    {
        "name": "list_work_orders",
        "description": "List tracked work orders (optionally filtered by "
                       "status OPEN/IN_PROGRESS/DONE/CANCELLED or by "
                       "equipment_id). Tier-1 live ledger read.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "equipment_id": {"type": "string"},
            },
        },
    },
    {
        "name": "update_work_order",
        "description": "Change a work order's status (OPEN / IN_PROGRESS / "
                       "DONE / CANCELLED) when the engineer reports "
                       "progress or completion.",
        "input_schema": {
            "type": "object",
            "properties": {
                "work_order_id": {"type": "string"},
                "status": {"type": "string",
                           "enum": ["OPEN", "IN_PROGRESS", "DONE",
                                    "CANCELLED"]},
            },
            "required": ["work_order_id", "status"],
        },
    },
    {
        "name": "rank_maintenance_priorities",
        "description": "Section-5.2 prioritizer: ranks every known asset by "
                       "a transparent weighted fusion of process criticality "
                       "(register), delay severity (delay log), spares "
                       "availability + procurement lead time (CMMS), and "
                       "live condition risk (health scan). Returns per-factor "
                       "sub-scores, weights, sources and honesty flags. Use "
                       "for 'what should we fix first', bottleneck "
                       "prioritization, and maintenance-plan ordering.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "scan_plant_health",
        "description": "Fleet-wide scan: health score 0-100 and fired "
                       "anomaly layers for every equipment/parameter pair "
                       "with stored readings. Use for plant-level triage, "
                       "'what should I worry about', and proactive checks. "
                       "Tier-1 evidence; scores are a stated heuristic.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "estimate_rul_weibull",
        "description": "Weibull conditional RUL (rotational-wear branch of "
                       "Section 7). REQUIRES user/OEM-supplied shape beta, "
                       "scale eta_hours and current age_hours — never assume "
                       "these. Returns median residual life + 80% band + "
                       "current hazard rate.",
        "input_schema": {
            "type": "object",
            "properties": {
                "beta": {"type": "number", "description": "Weibull shape"},
                "eta_hours": {"type": "number",
                              "description": "characteristic life, hours"},
                "age_hours": {"type": "number",
                              "description": "current accumulated age, hours"},
            },
            "required": ["beta", "eta_hours", "age_hours"],
        },
    },
    {
        "name": "estimate_rul_arrhenius",
        "description": "Arrhenius thermal-aging RUL (Section 7). REQUIRES "
                       "user/OEM-supplied activation energy ea_ev, design "
                       "temperature, actual temperature, rated design life "
                       "and hours consumed — never assume these. Returns "
                       "acceleration factor and remaining life.",
        "input_schema": {
            "type": "object",
            "properties": {
                "ea_ev": {"type": "number",
                          "description": "activation energy, eV"},
                "design_temp_c": {"type": "number"},
                "actual_temp_c": {"type": "number"},
                "design_life_hours": {"type": "number"},
                "hours_consumed": {"type": "number"},
            },
            "required": ["ea_ev", "design_temp_c", "actual_temp_c",
                         "design_life_hours", "hours_consumed"],
        },
    },
    {
        "name": "estimate_rul_pf_interval",
        "description": "P-F interval RUL (Section 7: alarm already "
                       "tripped). REQUIRES the OEM/standard P-F interval "
                       "and elapsed time since the P-condition was "
                       "detected. Returns remaining window + fraction "
                       "consumed; flags WINDOW_EXPIRED.",
        "input_schema": {
            "type": "object",
            "properties": {
                "pf_interval_hours": {"type": "number"},
                "hours_since_p_detected": {"type": "number"},
            },
            "required": ["pf_interval_hours", "hours_since_p_detected"],
        },
    },
]

TOOL_SCHEMAS = [
    {
        "name": "search_knowledge_base",
        "description": "Retrieve relevant chunks from the plant knowledge "
                       "base (OEM manuals, SOPs, maintenance history, failure "
                       "reports). Returns provenance (doc name + chunk id) "
                       "for VULCAN Section 4B citations.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "search query, e.g. 'mold "
                                         "oscillator vibration limits'"},
                "top_k": {"type": "integer", "default": 4},
            },
            "required": ["query"],
        },
    },
    {
        "name": "detect_anomalies",
        "description": "Run the layered anomaly engine (threshold / z-score "
                       "/ CUSUM / trend) on stored sensor readings for one "
                       "equipment+parameter. Tier-1 evidence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "equipment_id": {"type": "string"},
                "parameter": {"type": "string",
                              "description": "e.g. 'vibration_mm_s', "
                                             "'bearing_temp_C'"},
            },
            "required": ["equipment_id", "parameter"],
        },
    },
    {
        "name": "estimate_rul",
        "description": "Linear-regression drift RUL with 80% CI for one "
                       "equipment+parameter, extrapolated to the critical "
                       "threshold. Returns INCALCULABLE with a minimum-data "
                       "plan when inputs are insufficient.",
        "input_schema": {
            "type": "object",
            "properties": {
                "equipment_id": {"type": "string"},
                "parameter": {"type": "string"},
                "critical_threshold": {
                    "type": "number",
                    "description": "optional override; otherwise read from "
                                   "thresholds.csv"},
            },
            "required": ["equipment_id", "parameter"],
        },
    },
    {
        "name": "analyze_delay_log",
        "description": "Pareto-rank delay causes, TBF trend per asset, "
                       "chronic repeat offenders, and the plant bottleneck "
                       "candidate from data/delay_log.csv. Tier-2 evidence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "equipment_id": {
                    "type": "string",
                    "description": "optional filter to one asset"},
            },
        },
    },
    {
        "name": "query_spares",
        "description": "Look up parts in the CMMS spares catalog by "
                       "functional description: stock qty, location, lead "
                       "time. Tier-1 (live stock read).",
        "input_schema": {
            "type": "object",
            "properties": {"search_term": {"type": "string"}},
            "required": ["search_term"],
        },
    },
    {
        "name": "record_feedback",
        "description": "Persist engineer feedback (CONFIRMED / PARTIAL / "
                       "INCORRECT) on a VULCAN diagnosis to the learning "
                       "store (survives across sessions).",
        "input_schema": {
            "type": "object",
            "properties": {
                "report_id": {"type": "string"},
                "equipment_class": {"type": "string"},
                "failure_mode": {"type": "string"},
                "verdict": {"type": "string",
                            "enum": ["CONFIRMED", "PARTIAL", "INCORRECT"]},
                "vulcan_confidence": {"type": "string"},
                "correction_detail": {"type": "string"},
            },
            "required": ["report_id", "equipment_class", "failure_mode",
                         "verdict"],
        },
    },
    {
        "name": "get_feedback_history",
        "description": "Retrieve past engineer verdicts for an equipment "
                       "class / failure mode so Section-13 confidence "
                       "adjustments use real history. Tier-2 evidence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "equipment_class": {"type": "string"},
                "failure_mode": {"type": "string"},
            },
        },
    },
]

TOOL_SCHEMAS += _EXTRA_SCHEMAS


def _runtime_context() -> str:
    """Injected as the first system block so Report IDs never invent a date."""
    now = datetime.now(timezone.utc)
    return (f"[HOST RUNTIME CONTEXT] Current UTC datetime: "
            f"{now.isoformat(timespec='seconds')}. Use this date for Report "
            f"IDs (Section 9). Tools listed in this API request are GENUINE "
            f"host tools per Section 3B — call them per rule T1.")


def _build_system() -> list[dict]:
    """System blocks in cache-correct order: the static prompt FIRST with the
    cache breakpoint, the volatile timestamped context AFTER it — so the
    cached prefix is byte-identical across calls (real cache hits).

    v8: the learned-priors block (aggregated engineer feedback) is appended
    automatically, closing the feedback loop with no extra tool round."""
    blocks = [
        {"type": "text", "text": load_system_prompt(),
         "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": _runtime_context()},
    ]
    priors = learning_priors_block()
    if priors:
        blocks.append({"type": "text", "text": priors})
    return blocks


# ──────────────────────── v8 efficiency machinery ────────────────────────

# Tool results older than this many user turns are compacted to a stub so the
# context window doesn't carry stale KB chunks / fleet scans forever. The
# model's own text answers are kept verbatim (they hold the conversation).
COMPACT_KEEP_TURNS = 2
COMPACT_MIN_CHARS = 600           # only compact results bigger than this

RETRYABLE = (anthropic.APIConnectionError, anthropic.APITimeoutError,
             anthropic.RateLimitError, anthropic.InternalServerError)


def _compacted_stub(raw: str) -> str:
    try:
        data = json.loads(raw)
        status = data.get("status", "OK")
    except Exception:
        status = "OK"
    return json.dumps({
        "status": status,
        "compacted": True,
        "note": "full result elided from context to save tokens (v8 "
                "compaction); re-run the tool if fresh detail is needed",
    })


class VulcanOrchestrator:
    """Holds conversation state and runs the agentic tool loop per turn.

    v8 upgrades over v7:
      * parallel tool execution (multiple tool_use blocks run concurrently)
      * automatic context compaction (old bulky tool results -> stubs)
      * streaming (`ask_stream`) so the UI renders tokens as they arrive
      * retry with exponential backoff on transient API errors
      * actionable message on authentication failure (the 401 case)
      * learned-priors auto-injection via _build_system()
    """

    def __init__(self, model: str | None = None):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise RuntimeError(
                "ANTHROPIC_API_KEY is not set. Export it before running "
                "(see README.md / .env.example)."
            )
        self.client = anthropic.Anthropic()
        self.model = model or ANTHROPIC_MODEL
        self.messages: list[dict] = []
        self.tool_trace: list[dict] = []  # for the UI's transparency panel
        self._turn_index: list[int] = []  # message index where each turn starts

    # ── context compaction ──────────────────────────────────────────────
    def _compact_history(self) -> None:
        """Replace bulky tool_result payloads from turns older than
        COMPACT_KEEP_TURNS with one-line stubs. Final assistant prose is
        never touched, so conversational memory is preserved."""
        if len(self._turn_index) <= COMPACT_KEEP_TURNS:
            return
        cutoff = self._turn_index[-COMPACT_KEEP_TURNS]
        for msg in self.messages[:cutoff]:
            if msg["role"] != "user" or not isinstance(msg["content"], list):
                continue
            for item in msg["content"]:
                if (isinstance(item, dict)
                        and item.get("type") == "tool_result"
                        and isinstance(item.get("content"), str)
                        and len(item["content"]) > COMPACT_MIN_CHARS
                        and '"compacted": true' not in item["content"]):
                    item["content"] = _compacted_stub(item["content"])

    # ── resilient API calls ─────────────────────────────────────────────
    _AUTH_MSG = ("Anthropic rejected the API key (401 invalid x-api-key). "
                 "Fix: paste a valid key from console.anthropic.com in "
                 "the sidebar, or export ANTHROPIC_API_KEY. Keys start "
                 "with 'sk-ant-' and must have no surrounding spaces/"
                 "quotes.")

    def _create(self, system: list[dict], max_retries: int = 3):
        """Non-streaming call (used by tool rounds in `ask`)."""
        delay = 2.0
        for attempt in range(max_retries + 1):
            try:
                return self.client.messages.create(
                    model=self.model, max_tokens=MAX_TOKENS, system=system,
                    tools=TOOL_SCHEMAS, messages=self.messages)
            except anthropic.AuthenticationError as exc:
                raise RuntimeError(self._AUTH_MSG) from exc
            except RETRYABLE as exc:
                if attempt == max_retries:
                    raise RuntimeError(
                        f"Anthropic API unavailable after "
                        f"{max_retries + 1} attempts: {exc}") from exc
                time.sleep(delay)
                delay *= 2

    def _stream_round(self, system: list[dict], max_retries: int = 3):
        """ONE genuinely streamed model round (v9). Generator yielding
        ('delta', text) events as the server emits tokens, then a single
        ('message', final_message) event.

        v8 claimed "token-by-token" rendering but actually made a blocking
        `messages.create` call and re-chunked the finished text in 80-char
        slices — cosmetic streaming. v9 uses the real streaming API: every
        text delta is yielded the moment the server emits it, so
        time-to-first-token is network latency, not full-generation time.

        Retry policy is duplication-safe: transient errors are retried with
        exponential backoff ONLY if no token has been surfaced yet; once any
        text has reached the caller, a mid-stream failure is raised instead
        of retried (a retry would replay already-shown text).
        """
        delay = 2.0
        for attempt in range(max_retries + 1):
            emitted = False
            try:
                with self.client.messages.stream(
                        model=self.model, max_tokens=MAX_TOKENS,
                        system=system, tools=TOOL_SCHEMAS,
                        messages=self.messages) as stream:
                    for text in stream.text_stream:
                        if text:
                            emitted = True
                            yield ("delta", text)
                    yield ("message", stream.get_final_message())
                    return
            except anthropic.AuthenticationError as exc:
                raise RuntimeError(self._AUTH_MSG) from exc
            except RETRYABLE as exc:
                if emitted or attempt == max_retries:
                    raise RuntimeError(
                        f"Anthropic API streaming failed"
                        f"{' mid-stream' if emitted else ''} "
                        f"(attempt {attempt + 1}): {exc}") from exc
                time.sleep(delay)
                delay *= 2

    # ── tool execution (parallel) ───────────────────────────────────────
    def _run_tools(self, blocks: list) -> list[dict]:
        calls = [b for b in blocks if b.type == "tool_use"]

        def run_one(block):
            impl = TOOL_IMPLS.get(block.name)
            t0 = time.perf_counter()
            try:
                out = impl(**block.input) if impl else \
                    {"status": "ERROR",
                     "message": f"unknown tool {block.name}"}
            except Exception as exc:  # tool errors surfaced, not hidden
                out = {"status": "ERROR", "message": str(exc)}
            return block, out, round((time.perf_counter() - t0) * 1000)

        if len(calls) == 1:                       # no thread overhead for 1
            executed = [run_one(calls[0])]
        else:
            with ThreadPoolExecutor(max_workers=min(4, len(calls))) as pool:
                executed = list(pool.map(run_one, calls))

        results = []
        for block, out, ms in executed:
            self.tool_trace.append(
                {"tool": block.name, "input": block.input,
                 "output_status": out.get("status", "OK"), "ms": ms})
            results.append(
                {"type": "tool_result", "tool_use_id": block.id,
                 "content": json.dumps(out, default=str)})
        return results

    # ── public API ──────────────────────────────────────────────────────
    def ask(self, user_text: str) -> str:
        """One user turn → final assistant text (after any tool rounds).

        Non-streaming path (sentinel --with-llm, agent evals, CLI piping):
        only the FINAL round's text is returned, exactly as before.
        """
        self._turn_index.append(len(self.messages))
        self.messages.append({"role": "user", "content": user_text})
        self._compact_history()
        system = _build_system()

        for _ in range(MAX_TOOL_ROUNDS):
            resp = self._create(system)
            self.messages.append(
                {"role": "assistant", "content": resp.content})
            if resp.stop_reason != "tool_use":
                return "".join(b.text for b in resp.content
                               if b.type == "text")
            self.messages.append({"role": "user",
                                  "content": self._run_tools(resp.content)})
        return ("⚠️ Tool-round limit reached without a final answer. "
                "Partial results are in the tool trace — please rephrase "
                "or narrow the request.")

    def ask_stream(self, user_text: str):
        """Generator: yields text the moment the API emits it (TRUE
        streaming, v9 — see _stream_round). Tool rounds run between the
        streamed segments; any model preamble before a tool call is shown
        live too, so the user watches the agent think → act → conclude."""
        self._turn_index.append(len(self.messages))
        self.messages.append({"role": "user", "content": user_text})
        self._compact_history()
        system = _build_system()

        for _ in range(MAX_TOOL_ROUNDS):
            resp = None
            emitted_this_round = False
            for kind, payload in self._stream_round(system):
                if kind == "delta":
                    emitted_this_round = True
                    yield payload
                else:
                    resp = payload
            self.messages.append(
                {"role": "assistant", "content": resp.content})

            if resp.stop_reason != "tool_use":
                return
            if emitted_this_round:
                yield "\n\n"          # keep preamble & synthesis readable
            self.messages.append({"role": "user",
                                  "content": self._run_tools(resp.content)})

        yield ("⚠️ Tool-round limit reached without a final answer. "
               "Partial results are in the tool trace — please rephrase "
               "or narrow the request.")
