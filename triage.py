"""triage.py — TRIAGE step: rank scored opportunities, pick the single top one.

S-E2-3 (ER-style triage). Orders ``status='scored'`` buildable opportunities by
``triage_score`` (= urgency x payability, DB-generated) desc, then ``pain_reach``
desc, then ``created_at`` asc for a deterministic tie-break. Picks exactly ONE and
sets it ``triaged``; the lower-ranked ones are marked ``queued`` with a logged
reason. There is NO ``is_primed`` special-casing — determinism comes from the
scores. Fully try/except-isolated so it never crashes the loop.
"""

from __future__ import annotations

from typing import Optional

import db

# Only genuine-gap verdicts are eligible to build; POINT_FREE never reaches here.
_BUILD_VERDICTS = ["REAL_GAP", "UNDERCUT", "BUILD_SIMPLE"]


def pick_top() -> Optional[dict]:
    """Select the single highest-value scored opportunity and mark it ``triaged``.

    Returns the chosen opportunity row, or ``None`` if nothing is scored (the loop
    returns early in that case).
    """
    try:
        rows = (
            db.select("opportunities", "*")
            .eq("status", "scored")
            .in_("verdict", _BUILD_VERDICTS)
            .order("triage_score", desc=True)
            .order("pain_reach", desc=True)
            .order("created_at")
            .limit(1)
            .execute()
            .data
            or []
        )
        if not rows:
            return None

        chosen = rows[0]
        cid = chosen["id"]
        updated = db.update_row("opportunities", {"id": cid}, {"status": "triaged"})
        chosen = updated or {**chosen, "status": "triaged"}

        db.log_activity(
            chosen.get("signal_id"),
            "triaged",
            f"chose opportunity {cid} (score {chosen.get('triage_score')})",
            {"chosen": cid},
        )

        _queue_others(cid)
        return chosen
    except Exception as exc:  # noqa: BLE001 — never crash the loop
        db.log_activity(None, "triaged", f"pick_top error: {exc}")
        return None


def _queue_others(chosen_id: str) -> None:
    """Mark the remaining scored buildable opportunities ``queued`` with a reason.

    Best-effort and isolated: a failure here must never undo the chosen pick.
    """
    try:
        others = (
            db.select("opportunities", "id,signal_id")
            .eq("status", "scored")
            .in_("verdict", _BUILD_VERDICTS)
            .execute()
            .data
            or []
        )
        for o in others:
            oid = o.get("id")
            if not oid or oid == chosen_id:
                continue
            db.update_row(
                "opportunities",
                {"id": oid},
                {"status": "queued", "drop_reason": "outranked this cycle"},
            )
            db.log_activity(
                o.get("signal_id"),
                "triaged",
                f"queued opportunity {oid} — outranked this cycle",
                {"queued": oid},
            )
    except Exception as exc:  # noqa: BLE001 — losers are non-critical
        db.log_activity(None, "triaged", f"queue_others error: {exc}")
