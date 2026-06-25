"""app.py — Modal app: scheduled loop + web endpoints.

Wires together S-E3-2 (serve tools), S-E1-4 (scheduled unattended loop),
S-E4-3 (PayPal sandbox create/capture), and S-E6-3 (kill switch).

    modal deploy incline/app.py   # schedule + endpoints; survives walk-away
    modal run    incline/app.py   # dry run: seed primed scenario + one pass

One modal.App. A scheduled function drives run_loop() unattended. A single
FastAPI ASGI app serves generated tools at /t/<id>, runs the synchronous PayPal
sandbox capture (/pay/create, /pay/capture), seeds the primed scenario (/seed),
and exposes the kill switch (/kill, /resume). Secrets come from the Modal Secret
`incline-secrets` — never from code.
"""

from __future__ import annotations

import os
from pathlib import Path

import modal

HERE = Path(__file__).parent

# Bake deps + the local source into the image. Exclude secrets and caches —
# the real values are injected at runtime from the Modal Secret.
image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "supabase>=2.4",
        "openai>=1.40",
        "requests>=2.31",
        "fastapi>=0.110",
    )
    .add_local_dir(
        HERE,
        remote_path="/root/incline",
        ignore=[".env", "**/.env", "__pycache__", "**/__pycache__/**", "*.pyc", "**/*.pyc"],
    )
)

app = modal.App("incline")
secret = modal.Secret.from_name("incline-secrets")

# The unattended cadence. Edit before deploy if you want a faster demo loop.
LOOP_PERIOD_SECONDS = 60


def _ensure_path() -> None:
    """Make the mounted source importable inside the container."""
    import sys

    if "/root/incline" not in sys.path:
        sys.path.insert(0, "/root/incline")


@app.function(image=image, secrets=[secret], schedule=modal.Period(seconds=LOOP_PERIOD_SECONDS))
def scheduled_loop() -> None:
    """The hands-off driver — keeps firing after the operator walks away."""
    _ensure_path()
    import loop

    loop.run_loop()


@app.function(image=image, secrets=[secret])
def run_once() -> None:
    """Seed the primed scenario + run one full pass (rehearsal / pre-warm)."""
    _ensure_path()
    import loop

    loop.dry_run()


@app.local_entrypoint()
def main() -> None:
    """`modal run incline/app.py` → a single dry run against the primed scenario."""
    run_once.remote()


@app.function(image=image, secrets=[secret])
@modal.asgi_app()
def web():
    """Serve tools + PayPal capture + seed/kill controls."""
    _ensure_path()

    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

    import db
    import guardrails
    import ledger
    import listener
    import paypal

    api = FastAPI(title="Incline", docs_url=None, redoc_url=None)
    BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")

    def _fetch_tool(tool_id: str):
        res = db.select("tools", "*").eq("id", tool_id).limit(1).execute()
        return res.data[0] if res.data else None

    def _sale_exists(order_id: str) -> bool:
        if not order_id:
            return False
        res = db.select("sales", "id").eq("paypal_order_id", order_id).limit(1).execute()
        return bool(res.data)

    # ── DEPLOY: serve the generated/golden tool (S-E3-2) ────────────────
    @api.get("/t/{tool_id}", response_class=HTMLResponse)
    def serve_tool(tool_id: str):
        tool = _fetch_tool(tool_id)
        if not tool:
            return HTMLResponse("<h1>Tool not found</h1>", status_code=404)
        html = (
            tool["html"]
            .replace("{{BASE_URL}}", BASE_URL)
            .replace("{{TOOL_ID}}", tool_id)
        )
        return HTMLResponse(html)

    # ── SELL: PayPal sandbox order create (S-E4-3) ──────────────────────
    @api.post("/pay/create")
    async def pay_create(request: Request):
        body = await request.json()
        tool_id = body.get("tool_id")
        tool = _fetch_tool(tool_id)
        if not tool:
            return JSONResponse({"error": "tool not found"}, status_code=404)
        price = float(tool["price"])
        # Guardrail: the agent cannot self-authorize a price above the cap.
        if not guardrails.assert_price_ok(price, tool.get("signal_id")):
            return JSONResponse({"error": "price exceeds guardrail cap"}, status_code=403)
        return_url = f"{BASE_URL}/pay/capture?tool_id={tool_id}"
        cancel_url = f"{BASE_URL}/t/{tool_id}"
        try:
            order = paypal.create_order(price, tool.get("currency", "GBP"), return_url, cancel_url)
            return JSONResponse({"approval_url": order["approval_url"]})
        except Exception as exc:  # noqa: BLE001
            db.log_activity(tool.get("signal_id"), "blocked", f"pay/create failed: {exc}")
            return JSONResponse({"error": str(exc)}, status_code=502)

    # ── EARN: synchronous capture → ledger row → unlock (S-E4-3, S-E5-1) ─
    @api.get("/pay/capture")
    def pay_capture(token: str = "", tool_id: str = "", PayerID: str = ""):
        order_id = token
        tool = _fetch_tool(tool_id)
        try:
            cap = paypal.capture_order(order_id)
        except Exception as exc:  # noqa: BLE001
            # A duplicate return on an already-captured order still unlocks.
            if _sale_exists(order_id):
                return RedirectResponse(f"{BASE_URL}/t/{tool_id}?unlocked=1", status_code=303)
            sig = tool.get("signal_id") if tool else None
            db.log_activity(sig, "blocked", f"capture failed: {exc}")
            return RedirectResponse(f"{BASE_URL}/t/{tool_id}", status_code=303)

        if cap.get("status") == "COMPLETED" and tool:
            ledger.record_sale(order_id, tool)  # idempotent on paypal_order_id
            db.log_activity(
                tool.get("signal_id"),
                "paid",
                f"SALE £{float(tool['price']):.2f}",
                {"amount": float(tool["price"]), "order_id": order_id},
            )
            return RedirectResponse(f"{BASE_URL}/t/{tool_id}?unlocked=1", status_code=303)
        return RedirectResponse(f"{BASE_URL}/t/{tool_id}", status_code=303)

    # ── Primed scenario trigger (S-E1-2 — <10s on demand) ───────────────
    @api.post("/seed")
    def seed():
        rows = listener.seed_primed()
        return {"seeded": len(rows)}

    # ── Kill switch (S-E6-3) ────────────────────────────────────────────
    @api.post("/kill")
    def kill():
        guardrails.set_paused(True)
        return {"paused": True}

    @api.post("/resume")
    def resume():
        guardrails.set_paused(False)
        return {"paused": False}

    @api.get("/health")
    def health():
        return {"ok": True}

    return api
