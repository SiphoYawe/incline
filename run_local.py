"""run_local.py — drive Incline's loop on REAL posts from THIS machine.

Reddit blocks Modal's datacenter IP, so the live agent runs here, where the
agent-reach CLIs (rdt / twitter), their session cookies, and a residential IP
live. It still uses the Modal-hosted Hermes model, Supabase, and the Modal
tool/pay endpoints over HTTPS. REPLY_MODE=post -> it posts real comments via
``rdt comment`` / ``twitter reply`` (respecting the posts/hour guardrail).

  python3 run_local.py             # one pass
  python3 run_local.py --loop 90   # continuous, every 90s
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def _load_env() -> None:
    p = Path(__file__).parent / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        os.environ[k.strip()] = v.strip()


def _load_x_cookies() -> None:
    """twitter-cli reads TWITTER_AUTH_TOKEN/TWITTER_CT0 from env; agent-reach
    stores the captured session in ~/.agent-reach/config.yaml."""
    try:
        import yaml

        cfg = yaml.safe_load((Path.home() / ".agent-reach" / "config.yaml").read_text())
        if cfg.get("twitter_auth_token"):
            os.environ["TWITTER_AUTH_TOKEN"] = str(cfg["twitter_auth_token"])
        if cfg.get("twitter_ct0"):
            os.environ["TWITTER_CT0"] = str(cfg["twitter_ct0"])
    except Exception as exc:  # noqa: BLE001
        print(f"[run_local] X cookie load skipped: {exc}")


_load_env()
_load_x_cookies()
os.environ["REPLY_MODE"] = "post"  # LIVE commenting via the authed CLIs

import loop  # noqa: E402  (after env is loaded)


def main() -> None:
    if "--loop" in sys.argv:
        period = int(sys.argv[sys.argv.index("--loop") + 1])
        print(f"[run_local] live loop every {period}s · REPLY_MODE=post", flush=True)
        while True:
            try:
                loop.run_loop()
            except Exception as exc:  # noqa: BLE001
                print(f"[run_local] pass failed: {exc}", flush=True)
            time.sleep(period)
    else:
        loop.run_loop()
        print("[run_local] one pass complete", flush=True)


if __name__ == "__main__":
    main()
