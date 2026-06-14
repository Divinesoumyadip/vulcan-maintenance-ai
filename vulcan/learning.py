"""Automatic learning priors (v8) — the feedback loop, closed.

v7 stored engineer feedback faithfully in SQLite, but the loop was only
half-closed: the agent had to *choose* to call get_feedback_history before
the history influenced anything. A mentor would rightly call that
"learning on request", not learning.

v8 closes it: on every turn the orchestrator injects a compact, aggregated
summary of all engineer verdicts into the system context. Diagnoses that
engineers have repeatedly CONFIRMED earn an upward confidence prior;
diagnoses marked INCORRECT earn a downward one — automatically, with zero
extra tool calls and a few hundred tokens of overhead.

The block is derived data, never invented: each line states the verdict
counts it came from.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing

from vulcan.config import FEEDBACK_DB_PATH


MIN_EVIDENCE = 2   # verdicts needed before a prior may move confidence


def learning_priors_block(max_lines: int = 12) -> str:
    """Aggregate the feedback DB into a compact system-context block.

    Returns '' when no feedback exists (zero overhead until learning starts).

    v9 stringency: a confidence band may only move once at least
    MIN_EVIDENCE verdicts exist for the (class, mode) pair. v8 shifted a
    band on a single CONFIRMED/INCORRECT — one anecdote steering future
    diagnoses is exactly the over-fitting a reviewer would flag.
    """
    if not FEEDBACK_DB_PATH.exists():
        return ""
    try:
        with closing(sqlite3.connect(FEEDBACK_DB_PATH)) as conn:
            rows = conn.execute(
                "SELECT equipment_class, failure_mode, "
                "  SUM(verdict='CONFIRMED'), SUM(verdict='PARTIAL'), "
                "  SUM(verdict='INCORRECT') "
                "FROM feedback GROUP BY equipment_class, failure_mode "
                "ORDER BY COUNT(*) DESC LIMIT ?", (max_lines,)).fetchall()
    except sqlite3.Error:
        return ""
    if not rows:
        return ""

    lines = ["[LEARNED PRIORS — auto-injected from the engineer-feedback "
             "store; apply Section-13 confidence adjustments]"]
    for eq_cls, mode, ok, part, bad in rows:
        ok, part, bad = int(ok or 0), int(part or 0), int(bad or 0)
        total = ok + part + bad
        if not total:
            continue
        if total < MIN_EVIDENCE:
            hint = (f"insufficient evidence (n={total}) — keep confidence "
                    "unchanged; mention the single prior verdict only as "
                    "anecdote")
        elif ok and not bad:
            hint = "raise confidence one band (engineer-validated pattern)"
        elif bad and not ok:
            hint = "lower confidence one band and state the past miss"
        else:
            hint = "mixed record — keep confidence unchanged, cite both"
        lines.append(f"- {eq_cls} / {mode}: CONFIRMED x{ok}, PARTIAL x{part},"
                     f" INCORRECT x{bad} -> {hint}")
    return "\n".join(lines) if len(lines) > 1 else ""
