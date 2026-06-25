"""llm.py — single Hermes (Nous Research) client, prompt-routed per task.

Uses the OpenAI-compatible Nous inference API (Hermes 4) so the same client
serves qualify + score (JSON classification) and codegen. Default base URL is
the HOSTED Nous Portal endpoint — reachable from a cloud Modal container (the
Hermes Agent desktop server is localhost-only and won't work from Modal).

Everything is env-configurable:
  HERMES_API_KEY    — Nous Portal API key (Bearer)            [required]
  HERMES_BASE_URL   — default https://inference-api.nousresearch.com/v1
  HERMES_MODEL      — default Hermes-4-70B (confirm the exact id in your Portal)

The public interface (complete / complete_json / load_prompt / *_MODEL) is
unchanged, so qualifier.py and builder.py need no edits.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from openai import OpenAI

HERMES_BASE_URL = os.environ.get("HERMES_BASE_URL", "https://inference-api.nousresearch.com/v1")

# One Hermes model serves all three roles. Override per-role via env if you want
# a bigger model for codegen (e.g. Hermes-4-405B) vs a faster one for qualify.
_DEFAULT_MODEL = os.environ.get("HERMES_MODEL", "hermes")
QUALIFY_MODEL = os.environ.get("QUALIFY_MODEL", _DEFAULT_MODEL)
SCORE_MODEL = os.environ.get("SCORE_MODEL", _DEFAULT_MODEL)
CODEGEN_MODEL = os.environ.get("CODEGEN_MODEL", _DEFAULT_MODEL)

_PROMPT_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=1)
def client() -> OpenAI:
    """Cached OpenAI-compatible client pointed at the Nous Hermes endpoint."""
    return OpenAI(
        base_url=HERMES_BASE_URL,
        api_key=os.environ.get("HERMES_API_KEY") or "EMPTY",
    )


@lru_cache(maxsize=8)
def load_prompt(name: str) -> str:
    """Load a prompt template by basename (e.g. 'gap_verify')."""
    return (_PROMPT_DIR / f"{name}.txt").read_text(encoding="utf-8")


def complete(
    prompt: str,
    *,
    system: Optional[str] = None,
    model: str = QUALIFY_MODEL,
    max_tokens: int = 1024,
    temperature: float = 0.0,
) -> str:
    """Return Hermes's text output for a single user prompt (OpenAI chat shape)."""
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = client().chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return resp.choices[0].message.content or ""


def _extract_json(text: str) -> Optional[dict]:
    """Pull the first JSON object out of a model response, tolerating fences/prose."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = text[start : end + 1]
    if candidate is None:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def complete_json(
    prompt: str,
    *,
    system: Optional[str] = None,
    model: str = QUALIFY_MODEL,
    max_tokens: int = 1024,
    temperature: float = 0.0,
) -> Optional[dict]:
    """Like complete() but parse a strict JSON object; None on parse failure."""
    raw = complete(
        prompt,
        system=system,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return _extract_json(raw)
