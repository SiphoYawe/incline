"""reach.py — agent-reach CLI adapter (the "eyes" of the listener).

Thin, reliability-first wrapper around the ``agent-reach`` toolchain. It shells
out to the platform CLIs that agent-reach provisions and parses their JSON into
the listener's normalized signal shape ``{source, source_id, author, text, url}``.

Backends (verified live via ``agent-reach doctor --json``):
  * Reddit  -> ``rdt-cli`` (``rdt search "<q>" -r <sub> --json``)
  * X/Twit. -> ``twitter-cli`` (``twitter search "<q>" -t latest -n N --json``)

DEPLOYMENT CAVEAT: these are CLIs that must be installed and authenticated on the
host where the loop runs. They are present on the local dev box but NOT in the
Modal cloud loop, so callers MUST treat every function here as best-effort:
``available()`` reports whether a CLI is on PATH, and every search function
swallows errors and returns ``[]`` instead of raising. The listener falls back to
its plain-JSON / seeds path when these return nothing.

This module is pure I/O + parsing: no DB writes, no LLM, read-only.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

# How long to let a single CLI invocation run before we give up on it.
_CLI_TIMEOUT = 25  # seconds

# Max posts to request per query (kept small to stay polite + fast).
_PER_QUERY_LIMIT = 15


def available(cli: str) -> bool:
    """True if the named CLI (``rdt`` / ``twitter``) is on PATH."""
    return shutil.which(cli) is not None


def reddit_available() -> bool:
    return available("rdt")


def x_available() -> bool:
    return available("twitter")


def _run_json(argv: list[str]) -> Any:
    """Run ``argv`` and parse stdout as JSON. Returns ``None`` on any failure.

    Never raises — a missing CLI, non-zero exit, timeout, or unparseable output
    all collapse to ``None`` so callers can fall back cleanly.
    """
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=_CLI_TIMEOUT,
            check=False,
        )
    except Exception:  # noqa: BLE001 — FileNotFoundError, TimeoutExpired, OSError ...
        return None
    out = (proc.stdout or "").strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except Exception:  # noqa: BLE001 — partial / non-JSON output
        return None


def _norm_reddit(child: dict) -> dict | None:
    """Map one rdt-cli ``t3`` child (``{"data": {...}}``) to the signal shape."""
    data = (child or {}).get("data") or child or {}
    sid = data.get("id")
    if not sid:
        return None
    title = data.get("title") or ""
    selftext = data.get("selftext") or ""
    permalink = data.get("permalink") or ""
    url = data.get("url") or (
        ("https://reddit.com" + permalink) if permalink else ""
    )
    return {
        "source": "reddit",
        "source_id": sid,
        "author": data.get("author"),
        "text": f"{title}\n\n{selftext}".strip(),
        "url": url,
    }


def _iter_reddit_children(payload: Any) -> list[dict]:
    """rdt-cli returns either a Listing ({"data":{"data":{"children":[...]}}})
    or a flat list ({"data":[{...}, ...]}). Normalize both into a child list."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    # Flat list shape: data is already a list of post dicts.
    if isinstance(data, list):
        return [{"data": p} for p in data if isinstance(p, dict)]
    # Listing shape: data.data.children -> [{kind, data}, ...]
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, dict):
            children = inner.get("children")
            if isinstance(children, list):
                return [c for c in children if isinstance(c, dict)]
        children = data.get("children")
        if isinstance(children, list):
            return [c for c in children if isinstance(c, dict)]
    return []


def search_reddit(queries: list[str], subreddits: list[str] | None = None) -> list[dict]:
    """Search Reddit via rdt-cli for each query; return normalized signal dicts.

    If ``subreddits`` is given, each query is run once per subreddit (rdt-cli
    ``-r`` only takes a single subreddit). De-duplicated on ``source_id``.
    Returns ``[]`` (never raises) if rdt isn't available or every call fails.
    """
    if not reddit_available():
        return []
    seen: set[str] = set()
    out: list[dict] = []
    targets = subreddits or [None]  # type: ignore[list-item]
    for query in queries:
        if not query:
            continue
        for sub in targets:
            argv = ["rdt", "search", query, "--sort", "new",
                    "--limit", str(_PER_QUERY_LIMIT), "--json"]
            if sub:
                argv += ["-r", sub]
            payload = _run_json(argv)
            for child in _iter_reddit_children(payload):
                sig = _norm_reddit(child)
                if not sig:
                    continue
                if sig["source_id"] in seen:
                    continue
                seen.add(sig["source_id"])
                out.append(sig)
    return out


def _norm_x(tweet: dict) -> dict | None:
    """Map one twitter-cli tweet object to the signal shape."""
    if not isinstance(tweet, dict):
        return None
    sid = tweet.get("id")
    if not sid:
        return None
    author = (tweet.get("author") or {})
    screen = author.get("screenName") or author.get("name")
    url = f"https://x.com/{screen}/status/{sid}" if screen else f"https://x.com/i/status/{sid}"
    return {
        "source": "x",
        "source_id": str(sid),
        "author": screen,
        "text": (tweet.get("text") or "").strip(),
        "url": url,
    }


def search_x(queries: list[str]) -> list[dict]:
    """Search X/Twitter via twitter-cli for each query; return signal dicts.

    twitter-cli ``--json`` returns ``{"ok":..., "data":[ {tweet}, ... ]}``.
    De-duplicated on ``source_id``. Returns ``[]`` (never raises) if the CLI is
    unavailable or every call fails.
    """
    if not x_available():
        return []
    seen: set[str] = set()
    out: list[dict] = []
    for query in queries:
        if not query:
            continue
        argv = ["twitter", "search", query, "-t", "latest",
                "-n", str(_PER_QUERY_LIMIT), "--json"]
        payload = _run_json(argv)
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            continue
        for tweet in rows:
            sig = _norm_x(tweet)
            if not sig:
                continue
            if sig["source_id"] in seen:
                continue
            seen.add(sig["source_id"])
            out.append(sig)
    return out
