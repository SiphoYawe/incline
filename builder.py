"""builder.py — BUILD step: constrained single-file codegen + golden fallback.

The single most demo-risky step (architecture.md §7). Turns the chosen,
triaged opportunity into ONE self-contained HTML tool that runs entirely in the
buyer's browser — no server-side execution of model output, no RCE surface.

Codegen is constrained to a narrow archetype (a `text_transformer`, concretely a
CSV column splitter) so output is reliable. On ANY failure — spend cap hit,
timeout, API error, parse error, or a failed validation gate — `generate()`
falls back to the hand-verified golden tool. BUILD must NEVER dead-end on stage.

Public surface:
  - validate(html) -> bool          structural gate before storing/serving
  - golden_fallback(opportunity)    no-LLM-spend, known-good tool (S-E3-3)
  - generate(opportunity)           live codegen, falls back automatically (S-E3-1)

The serve_tool endpoint (S-E3-2, integrator) stores `tools.html` verbatim and
does a literal string replace of `{{BASE_URL}}` and `{{TOOL_ID}}` before serving
at `{BASE_URL}/t/<tool_id>`. The golden file and codegen output both honour that
contract — they emit those literal placeholders and the /pay/create button hook.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import db
import guardrails
import llm

ARCHETYPE = "text_transformer"
TOOL_PRICE = 9.00
TOOL_CURRENCY = "GBP"
TOOL_MODEL = "one_time"
MAX_HTML_BYTES = 60000

_GOLDEN_PATH = Path(__file__).parent / "golden" / "golden_tool.html"


# ── validation gate (architecture.md §7) ─────────────────────────────────
def validate(html: str) -> bool:
    """Structural gate before a tool is stored/served.

    True iff the HTML is non-empty, looks like a self-contained tool with the
    locked-result region and the pay-button hook, and fits the size budget.
    """
    if not html or not html.strip():
        return False
    if "<html" not in html or "<script" not in html:
        return False
    if 'id="locked-result"' not in html:
        return False
    if "/pay/create" not in html:
        return False
    if len(html) >= MAX_HTML_BYTES:
        return False
    return True


# ── golden fallback (S-E3-3) — spends NO LLM tokens ──────────────────────
def golden_fallback(opportunity: dict) -> Optional[dict]:
    """Supply the hand-verified golden tool. Used whenever live codegen is
    blocked or fails, and whenever the spend cap forbids an LLM call.

    Inserts a `tools` row with used_fallback=True, marks the opportunity built,
    logs a `built` activity row, and returns the tools row. Never raises.
    """
    try:
        html = _GOLDEN_PATH.read_text(encoding="utf-8")
        signal_id = opportunity.get("signal_id")
        tool = db.insert_row(
            "tools",
            {
                "opportunity_id": opportunity["id"],
                "signal_id": signal_id,
                "archetype": ARCHETYPE,
                "html": html,
                "price": TOOL_PRICE,
                "currency": TOOL_CURRENCY,
                "model": TOOL_MODEL,
                "used_fallback": True,
            },
        )
        db.update_row("opportunities", {"id": opportunity["id"]}, {"status": "built"})
        db.log_activity(
            signal_id,
            "built",
            "built (golden fallback) csv-splitter",
            {"used_fallback": True, "archetype": ARCHETYPE},
        )
        return tool
    except Exception as exc:  # noqa: BLE001 — BUILD must never dead-end
        print(f"[builder] golden_fallback failed: {exc}")
        return None


# ── codegen helpers ──────────────────────────────────────────────────────
def _strip_code_fences(text: str) -> str:
    """Remove any leading/trailing markdown code fences the model may emit."""
    s = (text or "").strip()
    fence = re.match(r"^```[a-zA-Z]*\s*\n(.*?)\n?```$", s, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    # tolerate a stray opening/closing fence without a perfect pair
    s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def _build_need_spec(opportunity: dict) -> str:
    """A compact need-spec string from the signal text + opportunity rationale."""
    signal_text = ""
    try:
        res = (
            db.select("signals", "text")
            .eq("id", opportunity["signal_id"])
            .limit(1)
            .execute()
        )
        if res.data:
            signal_text = (res.data[0].get("text") or "").strip()
    except Exception as exc:  # noqa: BLE001 — degrade to rationale-only
        print(f"[builder] could not fetch signal text: {exc}")

    rationale = (opportunity.get("rationale") or "").strip()
    parts = []
    if signal_text:
        parts.append(f'The person wrote: "{signal_text}"')
    if rationale:
        parts.append(f"Why this is a real gap: {rationale}")
    if not parts:
        parts.append(
            "Build a CSV column splitter: paste/upload a CSV, pick a column, "
            "split the rows into one file per distinct value in that column."
        )
    return "\n".join(parts)


# ── generate (S-E3-1) — live codegen, auto-fallback on ANY failure ───────
def generate(opportunity: dict) -> Optional[dict]:
    """BUILD: constrained codegen → one validated single-file HTML tool.

    Falls back to the golden tool on a blocked spend cap or ANY failure
    (timeout, API error, parse, failed validation). Never raises, never
    dead-ends — the loop always continues to DEPLOY→SELL with a working tool.
    """
    signal_id = opportunity.get("signal_id")

    # Spend cap gate — if blocked, use the no-LLM-spend golden fallback.
    if not guardrails.can_spend(guardrails.CODEGEN_COST_EST, signal_id):
        return golden_fallback(opportunity)

    try:
        need_spec = _build_need_spec(opportunity)
        prompt = (
            llm.load_prompt("codegen")
            .replace("{need_spec}", need_spec)
            .replace("{archetype}", ARCHETYPE)
        )

        raw = llm.complete(
            prompt,
            model=llm.CODEGEN_MODEL,
            max_tokens=8000,
            temperature=0.2,
        )
        html = _strip_code_fences(raw)

        if not validate(html):
            print("[builder] codegen output failed validation — using golden fallback")
            return golden_fallback(opportunity)

        # Success — count the spend, persist the live tool.
        guardrails.add_spend(guardrails.CODEGEN_COST_EST)
        tool = db.insert_row(
            "tools",
            {
                "opportunity_id": opportunity["id"],
                "signal_id": signal_id,
                "archetype": ARCHETYPE,
                "html": html,
                "price": TOOL_PRICE,
                "currency": TOOL_CURRENCY,
                "model": TOOL_MODEL,
                "used_fallback": False,
            },
        )
        db.update_row("opportunities", {"id": opportunity["id"]}, {"status": "built"})
        db.log_activity(
            signal_id,
            "built",
            "built csv-splitter (live codegen)",
            {"used_fallback": False, "archetype": ARCHETYPE},
        )
        return tool
    except Exception as exc:  # noqa: BLE001 — BUILD must never dead-end
        print(f"[builder] generate failed ({exc}) — using golden fallback")
        return golden_fallback(opportunity)
