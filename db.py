"""db.py — Supabase client + thin query helpers (S-E5-1).

One source of truth for all state. Modal writes with the service-role key
(RLS is disabled for the hackathon); the dashboard reads with the anon key.

This module is the single write path for the audit trail: `log_activity`
(S-E6-2) and `chain_for_signal` live here so every component logs the same way.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Optional

from supabase import Client, create_client


@lru_cache(maxsize=1)
def client() -> Client:
    """Cached Supabase client using the service-role key (Modal side)."""
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)


# ── generic CRUD ─────────────────────────────────────────────────────────
def insert_row(table: str, row: dict) -> Optional[dict]:
    res = client().table(table).insert(row).execute()
    return res.data[0] if res.data else None


def update_row(table: str, match: dict, patch: dict) -> Optional[dict]:
    q = client().table(table).update(patch)
    for col, val in match.items():
        q = q.eq(col, val)
    res = q.execute()
    return res.data[0] if res.data else None


def select(table: str, columns: str = "*"):
    """Return a query builder so callers can chain .eq/.order/.limit."""
    return client().table(table).select(columns)


# ── signals (LISTEN) ─────────────────────────────────────────────────────
def upsert_signal(sig: dict) -> Optional[dict]:
    """Idempotent ingest — upsert on the (source, source_id) unique key."""
    res = (
        client()
        .table("signals")
        .upsert(sig, on_conflict="source,source_id")
        .execute()
    )
    return res.data[0] if res.data else None


def signals_without_opportunity() -> list[dict]:
    """Signals that passed the intent filter but have not been qualified yet."""
    sigs = select("signals", "*").eq("intent_match", True).execute().data or []
    oppos = select("opportunities", "signal_id").execute().data or []
    done = {o["signal_id"] for o in oppos}
    return [s for s in sigs if s["id"] not in done]


# ── guardrail_limits (OVERSIGHT) ─────────────────────────────────────────
def get_guardrails() -> dict:
    res = select("guardrail_limits", "*").eq("id", 1).execute()
    return res.data[0] if res.data else {}


def update_guardrails(patch: dict) -> Optional[dict]:
    return update_row("guardrail_limits", {"id": 1}, patch)


def count_replies_last_hour() -> int:
    """Live posts/hour usage — counted from activity_log, not a stored column."""
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    res = (
        client()
        .table("activity_log")
        .select("id", count="exact")
        .eq("stage", "replied")
        .gte("created_at", cutoff)
        .execute()
    )
    return res.count or 0


# ── activity_log (AUDIT TRAIL — single write path, S-E6-2) ───────────────
def log_activity(
    signal_id: Optional[str],
    stage: str,
    detail: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
) -> Optional[dict]:
    """The one consistent write path for the audit trail + dashboard feed.

    Stages (locked vocabulary): heard | qualified | triaged | built |
    deployed | replied | paid | blocked | escalation.
    Never raises — logging must not crash the unattended loop.
    """
    try:
        return insert_row(
            "activity_log",
            {"signal_id": signal_id, "stage": stage, "detail": detail, "meta": meta},
        )
    except Exception as exc:  # noqa: BLE001 — audit log must be best-effort
        print(f"[activity_log] failed to write {stage}: {exc}")
        return None


def chain_for_signal(signal_id: str) -> list[dict]:
    """The full ordered decision chain for one signal (the audit proof)."""
    res = (
        client()
        .table("activity_log")
        .select("*")
        .eq("signal_id", signal_id)
        .order("created_at")
        .execute()
    )
    return res.data or []
