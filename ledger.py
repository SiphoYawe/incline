"""ledger.py — the revenue ledger (S-E5-1).

`record_sale` is idempotent on `paypal_order_id`: one PayPal capture = exactly
one `sales` row, even if the capture/return fires twice. This table is what the
live counter subscribes to over Supabase Realtime — the wow moment.
"""

from __future__ import annotations

from typing import Optional

import db


def _existing_sale(order_id: str) -> Optional[dict]:
    res = (
        db.client()
        .table("sales")
        .select("*")
        .eq("paypal_order_id", order_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def record_sale(order_id: str, tool: dict, source: Optional[str] = None) -> dict:
    """Write exactly one sale for a captured PayPal order.

    Idempotent: a second capture of the same `order_id` returns the existing
    row without inserting a duplicate (AC3 / DoD).
    """
    existing = _existing_sale(order_id)
    if existing:
        return existing

    if source is None:
        # Derive the origin channel from the tool's signal (best-effort).
        try:
            sig = (
                db.client()
                .table("signals")
                .select("source")
                .eq("id", tool.get("signal_id"))
                .limit(1)
                .execute()
            )
            source = sig.data[0]["source"] if sig.data else None
        except Exception:  # noqa: BLE001
            source = None

    row = {
        "tool_id": tool.get("id"),
        "signal_id": tool.get("signal_id"),
        "paypal_order_id": order_id,
        "amount": tool.get("price", 9.00),
        "currency": tool.get("currency", "GBP"),
        "model": tool.get("model", "one_time"),
        "source": source,
    }

    # Upsert guards against a race between the existence check and insert.
    res = (
        db.client()
        .table("sales")
        .upsert(row, on_conflict="paypal_order_id", ignore_duplicates=True)
        .execute()
    )
    if res.data:
        return res.data[0]
    # ignore_duplicates returns nothing on conflict — re-read the winning row.
    return _existing_sale(order_id) or row


def revenue_summary() -> dict:
    """Total revenue + sale count + last sale time in one query (the counter)."""
    res = db.client().table("revenue_summary").select("*").limit(1).execute()
    if res.data:
        return res.data[0]
    return {"total_revenue": 0, "sale_count": 0, "last_sale_at": None}
