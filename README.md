# Incline

**An autonomous business that listens for real demand, builds a tool to meet it, and sells it — hands-off.**

Incline watches public conversations for people describing a genuine frustration —
*"I wish there was a tool to split a CSV by column"* — decides whether it's worth
solving, builds a small web tool for it, puts it behind a fair paywall, and lets
the money land on a live counter. No one at the keyboard.

Its character is simple: **listen humbly, then act decisively.**

---

## What it does

Incline runs one continuous loop:

**Listen → Qualify → Triage → Build → Deploy → Sell → Earn**

1. **Listen** — pulls real demand-intent posts ("I wish there was a tool that…", "how do I…") from public feeds.
2. **Qualify (the ethical ear)** — for each need it first asks *does a good free tool already exist?* If yes, it **says so and points you there for free**. It only builds when there's a real gap, and undercuts overpriced incumbents.
3. **Triage** — ranks the qualified needs by how much pain they cause and how many people share them, and acts on the single most worthwhile one.
4. **Build** — generates a complete, self-contained single-file web tool for that need (constrained to safe, predictable tool types), with a verified fallback so a build never dead-ends.
5. **Deploy** — serves the tool instantly at its own public link.
6. **Sell** — replies with a genuinely useful free answer first, then a soft link. The tool itself runs on your real input so you can watch it work *before* you pay.
7. **Earn** — a real payment is captured and written to a durable ledger; a live dashboard counter ticks up the moment it lands.

## Features

- **Ethical-gap filter** — gives value away for free when a free option exists; only charges on a real gap. You can watch it choose *not* to sell.
- **Results-gated trust flow** — the buyer runs the tool on their own data and sees a true preview before any payment. Preview-then-pay, never pay-then-run.
- **Real payments** — full PayPal checkout flow, captured synchronously and recorded once (idempotent).
- **Live revenue dashboard** — a single glanceable screen showing total revenue, sale count, a real-time activity feed, and guardrail usage. The counter moves only on a real sale.
- **Hard guardrails, enforced in code** — a total-spend cap, a posts-per-hour cap, and a maximum price the agent **cannot raise on its own**, plus a single kill switch. When a limit would be crossed, the action is blocked, not just logged.
- **Full audit trail** — every decision (heard, qualified, built, replied, paid, blocked) is written to one ordered log you can replay per signal.
- **Runs unattended** — the loop is scheduled and keeps working after you walk away.

## How it's built

| Layer | Technology |
|---|---|
| Compute + scheduling + hosting | **Modal** (serverless Python, scheduled functions, web endpoints) |
| Database, ledger & realtime | **Supabase** (Postgres + Realtime) |
| The agent's brain (qualify / score / build) | **Hermes** (Nous Research, OpenAI-compatible) |
| Payments | **PayPal** |
| Generated tools & dashboard | Single self-contained HTML — no build step |

Everything lives in one small Python codebase plus a static dashboard. Generated
tools are plain self-contained HTML that runs entirely in the buyer's browser, so
there is no server-side execution of generated code.

## Getting started

See **[SETUP.md](SETUP.md)** for the accounts, keys, and step-by-step bring-up
(Supabase schema, Hermes/Nous key, PayPal sandbox, and Modal deploy).

Quick shape:

```bash
# 1. create the database
#    run schema.sql in your Supabase SQL editor
# 2. configure secrets (see .env.example) into a Modal secret named "incline-secrets"
modal secret create incline-secrets --from-dotenv .env
# 3. deploy the scheduled loop + endpoints
modal deploy app.py
# 4. one-off dry run
modal run app.py
```

## Safety & honesty

Payments run against PayPal's **sandbox** and are labelled as such throughout —
the full real payment flow, no real charge. The guardrails are real and enforced,
the ledger is real, and the ethical filter that gives free help for free is the
whole point.

---

*Incline — the business that inclines its ear.*
