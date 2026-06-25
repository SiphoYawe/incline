"""qualifier.py — the EAR: ethical gap-verify + pain x reach scoring.

S-E2-1 (gap-verify, the soul) and S-E2-2 (pain x reach scoring).

``gap_verify`` produces exactly one of four verdicts; ``POINT_FREE`` signals are
helped for free and NEVER built or charged. ``score`` assigns pain / reach /
urgency / payability (each 1-10); ``pain_reach`` and ``triage_score`` are
DB-generated columns and are NEVER written here. ``qualify_signal`` is the
QUALIFY orchestrator that ``run_loop`` calls — it is conservative on ambiguity
(never auto-builds a paid tool) and is fully try/except-isolated so it can never
crash the loop.
"""

from __future__ import annotations

from typing import Optional

import db
import guardrails
import llm

_VALID_VERDICTS = {"POINT_FREE", "BUILD_SIMPLE", "UNDERCUT", "REAL_GAP"}

# Deterministic payability bias by verdict class so payable gaps (UNDERCUT /
# REAL_GAP) reliably outrank BUILD_SIMPLE in triage (S-E2-2 / S-E2-3 AC1). The
# floor lifts genuine paid gaps; the ceiling caps "only-too-technical" builds.
_PAYABILITY_FLOOR = {"REAL_GAP": 8, "UNDERCUT": 8}
_PAYABILITY_CEIL = {"BUILD_SIMPLE": 5}


def _signal_text(signal: dict) -> str:
    return (signal or {}).get("text", "") or ""


def gap_verify(signal: dict) -> Optional[dict]:
    """LLM gap-verify -> ``{verdict, rationale, free_pointer?, incumbent?}`` or ``None``.

    Returns ``None`` on parse failure or an invalid/unknown verdict so the caller
    can default conservative (never auto-build on ambiguity).
    """
    try:
        prompt = llm.load_prompt("gap_verify").replace(
            "{signal_text}", _signal_text(signal)
        )
        out = llm.complete_json(prompt, model=llm.QUALIFY_MODEL, temperature=0.0)
        if not out:
            return None
        verdict = str(out.get("verdict", "")).strip().upper()
        if verdict not in _VALID_VERDICTS:
            return None
        return {
            "verdict": verdict,
            "rationale": (out.get("rationale") or "").strip(),
            "free_pointer": ((out.get("free_pointer") or "").strip() or None),
            "incumbent": ((out.get("incumbent") or "").strip() or None),
        }
    except Exception as exc:  # noqa: BLE001 — degrade to conservative None
        db.log_activity((signal or {}).get("id"), "qualified", f"gap_verify error: {exc}")
        return None


def score(signal: dict) -> Optional[dict]:
    """LLM pain/reach/urgency/payability scoring (each clamped to 1-10) or ``None``.

    Low temperature for stable, demo-reproducible scores. Returns the raw four
    axes; the verdict-based payability bias is applied by ``qualify_signal``.
    """
    try:
        prompt = llm.load_prompt("score").replace(
            "{signal_text}", _signal_text(signal)
        )
        out = llm.complete_json(prompt, model=llm.SCORE_MODEL, temperature=0.0)
        if not out:
            return None
        result: dict = {}
        for key in ("pain", "reach", "urgency", "payability"):
            result[key] = max(1, min(10, int(out.get(key))))
        return result
    except Exception as exc:  # noqa: BLE001 — degrade to conservative None
        db.log_activity((signal or {}).get("id"), "qualified", f"score error: {exc}")
        return None


def _bias_payability(payability: int, verdict: str) -> int:
    """Fold the verdict class into payability so paid gaps outrank simple builds."""
    p = payability
    floor = _PAYABILITY_FLOOR.get(verdict)
    if floor is not None:
        p = max(p, floor)
    ceil = _PAYABILITY_CEIL.get(verdict)
    if ceil is not None:
        p = min(p, ceil)
    return max(1, min(10, p))


def qualify_signal(signal: dict) -> Optional[dict]:
    """QUALIFY orchestrator: gap_verify -> (POINT_FREE help-only | score -> opportunity).

    Conservative on ambiguity: parse failures are dropped with a recorded reason
    and NEVER auto-built. Returns the persisted ``opportunities`` row, or ``None``
    on a drop / error. Never raises.
    """
    sid = (signal or {}).get("id")
    try:
        v = gap_verify(signal)

        # 1. Parse failure / invalid verdict -> conservative drop (never auto-build).
        if v is None:
            db.insert_row(
                "opportunities",
                {
                    "signal_id": sid,
                    "verdict": "POINT_FREE",  # safest non-build value for the NOT NULL col
                    "status": "dropped",
                    "drop_reason": "qualify parse failure",
                },
            )
            db.log_activity(
                sid, "qualified", "dropped — qualify parse failure", {"verdict": None}
            )
            return None

        verdict = v["verdict"]
        rationale = v.get("rationale") or ""

        # 2. POINT_FREE -> help-only reply, NEVER score, NEVER build (the soul).
        if verdict == "POINT_FREE":
            row = db.insert_row(
                "opportunities",
                {
                    "signal_id": sid,
                    "verdict": verdict,
                    "rationale": rationale,
                    "free_pointer": v.get("free_pointer"),
                    "status": "point_free",
                },
            )
            db.log_activity(
                sid,
                "qualified",
                f"pointed to FREE tool — {rationale}",
                {"verdict": "POINT_FREE"},
            )
            return row

        # 3. BUILD_SIMPLE / UNDERCUT / REAL_GAP -> score, then persist scored row.
        s = score(signal)
        if s is None:  # treat conservatively — drop, never auto-build on ambiguity
            db.insert_row(
                "opportunities",
                {
                    "signal_id": sid,
                    "verdict": verdict,
                    "rationale": rationale,
                    "status": "dropped",
                    "drop_reason": "score parse failure",
                },
            )
            db.log_activity(
                sid, "qualified", "dropped — score parse failure", {"verdict": verdict}
            )
            return None

        payability = _bias_payability(s["payability"], verdict)
        row = db.insert_row(
            "opportunities",
            {
                "signal_id": sid,
                "verdict": verdict,
                "rationale": rationale,
                "incumbent": v.get("incumbent"),
                "pain": s["pain"],
                "reach": s["reach"],
                "urgency": s["urgency"],
                "payability": payability,
                "status": "scored",
            },
        )
        # Move the visible spend meter only after the (billable) LLM calls succeeded.
        guardrails.add_spend(guardrails.QUALIFY_COST_EST + guardrails.SCORE_COST_EST)
        db.log_activity(
            sid,
            "qualified",
            f"{verdict} — {rationale}",
            {"verdict": verdict, "pain": s["pain"], "reach": s["reach"]},
        )
        return row
    except Exception as exc:  # noqa: BLE001 — never crash the loop
        db.log_activity(sid, "qualified", f"qualify_signal error: {exc}")
        return None
