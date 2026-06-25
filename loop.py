"""loop.py — the orchestrated autonomous sequence (architecture §4, S-E7-1).

`run_loop()` is invoked by Modal's scheduler (app.py). Every step is
try/except-isolated: one failure degrades, logs to activity_log, and continues —
a single bad signal never crashes the unattended run. The kill switch
(S-E6-3) is checked up front: when paused, the loop degrades to listen-only.

    LISTEN → QUALIFY (the ear) → TRIAGE → BUILD → DEPLOY → SELL
"""

from __future__ import annotations

import os

import builder
import db
import guardrails
import listener
import qualifier
import seller
import triage


def _base_url() -> str:
    return os.environ.get("BASE_URL", "").rstrip("/")


def run_loop() -> None:
    """One full pass of the autonomous loop. Safe to call on a schedule."""
    alive = guardrails.assert_alive()  # kill switch (S-E6-3)

    # 1 LISTEN — hear new demand (idempotent upsert; writes a 'heard' row).
    try:
        listener.poll_reddit()
        listener.poll_x()  # P1 stub — returns [] for MVP
    except Exception as exc:  # noqa: BLE001
        db.log_activity(None, "blocked", f"listen failed: {exc}")

    if not alive:
        # Paused: we still listen, but perform NO outbound action.
        db.log_activity(None, "blocked", "paused — listen-only (kill switch) · ✓ enforced")
        return

    # 2 QUALIFY (the ear) — gap-verify + score every un-qualified signal.
    try:
        for sig in db.signals_without_opportunity():
            opp = qualifier.qualify_signal(sig)
            # POINT_FREE → help-only free pointer, NEVER builds (the soul).
            if opp and opp.get("verdict") == "POINT_FREE":
                try:
                    seller.sell(opp, None)
                except Exception as exc:  # noqa: BLE001
                    db.log_activity(sig.get("id"), "blocked", f"help-only reply failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        db.log_activity(None, "blocked", f"qualify failed: {exc}")

    # 3 TRIAGE — pick the single highest-value opportunity to act on.
    try:
        top = triage.pick_top()
    except Exception as exc:  # noqa: BLE001
        db.log_activity(None, "blocked", f"triage failed: {exc}")
        top = None
    if not top:
        return  # nothing buildable this pass

    # 4 BUILD — constrained codegen (spend-gated) with automatic golden fallback.
    try:
        tool = builder.generate(top)
    except Exception as exc:  # noqa: BLE001
        db.log_activity(top.get("signal_id"), "blocked", f"build failed: {exc}")
        return
    if not tool:
        return

    # 5 DEPLOY — the endpoint serves tools.html at /t/<id>; record the URL.
    try:
        url = f"{_base_url()}/t/{tool['id']}"
        db.update_row("tools", {"id": tool["id"]}, {"url": url})
        tool["url"] = url
        db.log_activity(tool.get("signal_id"), "deployed", f"deployed {url}", {"url": url})
    except Exception as exc:  # noqa: BLE001
        db.log_activity(tool.get("signal_id"), "blocked", f"deploy failed: {exc}")

    # 6 SELL — value-first reply (DRAFT mode), respecting the posts/hour cap.
    try:
        seller.sell(top, tool)
    except Exception as exc:  # noqa: BLE001
        db.log_activity(top.get("signal_id"), "blocked", f"sell failed: {exc}")


def dry_run() -> None:
    """Seed the primed scenario, then run one full pass (S-E7-1 rehearsal)."""
    listener.seed_primed()
    run_loop()
