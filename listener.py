"""listener.py — LISTEN step: Reddit + X demand-intent ingest + primed seeding.

S-E1-1 (Reddit signal ingest), S-E1-2 (primed scenario seeding), S-E1-3 (X).

PRIMARY access path = the **agent-reach** CLIs (``reach.py``): real demand-intent
posts from Reddit (``rdt-cli``) and X/Twitter (``twitter-cli``). When those CLIs
aren't on PATH (or error), ``poll_reddit`` falls back to the plain Reddit JSON
HTTP path (``https://www.reddit.com/r/<sub>/new.json``), and ``poll_x`` simply
returns ``[]``. The intent filter is a cheap deterministic substring match on a
LOCKED phrase list — NEVER the LLM. Matched posts are normalized and upserted
into ``signals`` (idempotent on the ``(source, source_id)`` unique key).

DEPLOYMENT CAVEAT: agent-reach is a CLI toolchain that must be installed and
authenticated on the host where the loop runs. It is present on the local dev
box but is NOT installed in the Modal cloud loop environment — there the loop
transparently degrades to the plain Reddit JSON path (``poll_reddit``) and the
``seeds.json`` primed posts (``seed_primed``), and ``poll_x`` returns ``[]``.
Everything here is best-effort: a missing CLI must never crash the loop.

Every public function is try/except-isolated so a single bad poll never crashes
``run_loop``; on failure it logs to ``activity_log`` and returns ``[]``. This
module never calls the LLM and never posts to Reddit/X (read-only).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests

import db
import reach

# Locked demand-intent phrase list (S-E1-1 AC3). Case-insensitive substring match.
_INTENT_PHRASES = (
    "i wish there was",
    "does a tool exist",
    "is there anything that",
    "how do i",
)

_REDDIT_TIMEOUT = 10  # seconds


def _user_agent() -> str:
    return os.environ.get("REDDIT_USER_AGENT", "incline/0.1")


def _subreddits() -> list[str]:
    raw = os.environ.get("REDDIT_SUBREDDITS", "SomebodyMakeThis,Entrepreneur")
    return [s.strip() for s in raw.split(",") if s.strip()]


def _intent_filter(text: str) -> bool:
    """True if text contains demand-intent language. Cheap, deterministic, no LLM."""
    if not text:
        return False
    low = text.lower()
    return any(phrase in low for phrase in _INTENT_PHRASES)


def _normalize(post: dict) -> dict:
    """Map a Reddit JSON child post (reads ``post['data']``) to the signal shape."""
    data = (post or {}).get("data", {}) or {}
    title = data.get("title") or ""
    selftext = data.get("selftext") or ""
    permalink = data.get("permalink") or ""
    return {
        "source": "reddit",
        "source_id": data.get("id"),
        "author": data.get("author"),
        "text": f"{title}\n\n{selftext}".strip(),
        "url": "https://reddit.com" + permalink,
    }


def _upsert_signals(signals: list[dict]) -> list[dict]:
    """Run already-normalized signal dicts through intent filter -> upsert.

    Shared tail used by both the agent-reach path and the plain-JSON path so the
    filter/upsert/idempotency rules stay identical regardless of the source.
    """
    upserted: list[dict] = []
    for sig in signals or []:
        if not sig or not sig.get("source_id"):
            continue
        if not _intent_filter(sig.get("text", "")):
            continue  # non-demand post dropped (not persisted as a match)
        row = db.upsert_signal({**sig, "intent_match": True})
        if row:
            upserted.append(row)
    return upserted


def _poll_reddit_reach() -> list[dict]:
    """agent-reach (rdt-cli) Reddit path. Returns normalized signals.

    Searches with the LOCKED demand-intent phrases as queries, scoped to the
    configured subreddits. Raises nothing of its own beyond what ``reach``
    surfaces (which is already swallowed there) — caller wraps in try/except.
    """
    queries = list(_INTENT_PHRASES)
    return reach.search_reddit(queries, subreddits=_subreddits())


def _poll_reddit_json() -> list[dict]:
    """Fallback plain Reddit JSON HTTP path. Returns normalized signals.

    ``https://www.reddit.com/r/<sub>/new.json`` with a custom User-Agent. One bad
    subreddit is logged and skipped; never raises.
    """
    headers = {"User-Agent": _user_agent()}
    signals: list[dict] = []
    for sub in _subreddits():
        url = f"https://www.reddit.com/r/{sub}/new.json?limit=50"
        try:
            resp = requests.get(url, headers=headers, timeout=_REDDIT_TIMEOUT)
            resp.raise_for_status()
            children = resp.json().get("data", {}).get("children", []) or []
        except Exception as exc:  # noqa: BLE001 — one bad subreddit must not kill the poll
            db.log_activity(
                None, "heard", f"reddit poll failed for r/{sub}: {exc}", {"sub": sub}
            )
            continue
        for post in children:
            sig = _normalize(post)
            if sig.get("source_id"):
                signals.append(sig)
    return signals


def poll_reddit() -> list[dict]:
    """Poll Reddit for demand-intent posts; upsert matches.

    PREFERS agent-reach (rdt-cli) when its CLI is available, and falls back to the
    plain Reddit JSON HTTP path on any error or when the CLI is absent (e.g. the
    Modal cloud loop). Idempotent via the ``(source, source_id)`` unique key.
    Never calls the LLM, never posts to Reddit. On total failure logs it and
    returns ``[]``. Writes one ``heard`` activity row per poll.
    """
    try:
        signals: list[dict] = []
        used = "json"
        if reach.reddit_available():
            try:
                signals = _poll_reddit_reach()
                used = "agent-reach"
            except Exception as exc:  # noqa: BLE001 — fall back, never crash
                db.log_activity(
                    None, "heard", f"agent-reach reddit failed, falling back: {exc}", {}
                )
                signals = []
        if not signals:
            # CLI absent, errored, or returned nothing -> plain JSON fallback.
            signals = _poll_reddit_json()
            used = "json" if used != "agent-reach" else "agent-reach+json"
        upserted = _upsert_signals(signals)
        n = len(upserted)
        db.log_activity(None, "heard", f"heard {n} need(s)", {"n": n, "via": used})
        return upserted
    except Exception as exc:  # noqa: BLE001 — never crash the loop
        db.log_activity(None, "heard", f"poll_reddit failed: {exc}", {"n": 0})
        return []


def poll_x() -> list[dict]:
    """Poll X/Twitter for demand-intent posts via agent-reach (twitter-cli).

    Searches with the LOCKED demand-intent phrases. If the twitter-cli backend is
    unavailable (e.g. the Modal cloud loop) or any call errors, logs it and
    returns ``[]`` — never crashes. Same normalize/upsert/log path as Reddit,
    ``source='x'``. Idempotent on ``(source, source_id)``.
    """
    try:
        if not reach.x_available():
            db.log_activity(
                None, "heard", "x backend unavailable (twitter-cli not on PATH)", {"n": 0}
            )
            return []
        signals = reach.search_x(list(_INTENT_PHRASES))
        upserted = _upsert_signals(signals)
        n = len(upserted)
        db.log_activity(None, "heard", f"heard {n} X need(s)", {"n": n, "via": "agent-reach"})
        return upserted
    except Exception as exc:  # noqa: BLE001 — never crash the loop
        db.log_activity(None, "heard", f"poll_x failed: {exc}", {"n": 0})
        return []


def seed_primed() -> list[dict]:
    """Inject the hand-picked primed posts (seeds.json) as signals.

    Flagged ``is_primed=true`` but otherwise the identical signal shape and the
    SAME upsert path as organic signals — no downstream special-casing. Idempotent
    on ``(source, source_id)``, runs in well under 10s. On failure logs and
    returns ``[]``.
    """
    upserted: list[dict] = []
    try:
        seeds_path = Path(__file__).parent / "seeds.json"
        seeds = json.loads(seeds_path.read_text(encoding="utf-8"))
        for seed in seeds:
            source_id = (seed or {}).get("source_id")
            if not source_id:
                continue
            sig = {
                "source": seed.get("source", "reddit"),
                "source_id": source_id,
                "author": seed.get("author"),
                "text": seed.get("text", ""),
                "url": seed.get("url"),
                "is_primed": True,
                "intent_match": True,
            }
            row = db.upsert_signal(sig)
            if row:
                upserted.append(row)
        n = len(upserted)
        db.log_activity(
            None, "heard", f"heard {n} primed need(s)", {"n": n, "primed": True}
        )
        return upserted
    except Exception as exc:  # noqa: BLE001 — never crash the loop
        db.log_activity(None, "heard", f"seed_primed failed: {exc}", {"n": 0})
        return []
