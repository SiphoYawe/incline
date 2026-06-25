# Setup

Everything you create, the keys it produces, and where each one goes. Secrets
live in two places, never in committed code:

1. **`.env`** — your local store (gitignored). Copy `.env.example` to `.env` and fill it in.
2. **Modal Secret `incline-secrets`** — what the deployed app reads at runtime, created *from* `.env`.

The dashboard also takes two read-only values (`SUPABASE_URL`, `SUPABASE_ANON_KEY`)
pasted directly into `dashboard/index.html` (the anon key is public-safe).

| Tool | You create | Produces |
|---|---|---|
| **Supabase** | A project + run `schema.sql` | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_ANON_KEY` |
| **Hermes (Nous Research)** | A Nous Portal API key | `HERMES_API_KEY` |
| **PayPal** | A sandbox app + test accounts | `PAYPAL_CLIENT_ID`, `PAYPAL_SECRET`, `PAYPAL_BASE` |
| **Modal** | An account + token + the Secret | `BASE_URL` (after first deploy) |
| **Reddit** | *nothing* (plain JSON path) | `REDDIT_SUBREDDITS`, `REDDIT_USER_AGENT` |

---

## 1. Supabase — state, ledger, live counter
1. Create a project at <https://supabase.com/dashboard>.
2. **Settings → API**: copy the **Project URL**, the **`anon` public** key, and the **`service_role`** key → into `.env`.
3. **SQL Editor**: paste all of `schema.sql` and run it. This creates the tables + `revenue_summary` view, seeds the single `guardrail_limits` row (spend £15, price £15, 10 posts/hr), and enables Realtime on `sales` + `activity_log`.
4. Paste `SUPABASE_URL` + the **anon** key into the two placeholders at the top of `dashboard/index.html`.

## 2. Hermes — the agent's brain (qualify / score / build)
Incline calls Hermes through the **OpenAI-compatible Nous inference API**.
1. Get a key from the **Nous Portal** (<https://portal.nousresearch.com>).
2. Set `HERMES_API_KEY` in `.env`. Defaults (overridable in `.env`):
   - `HERMES_BASE_URL=https://inference-api.nousresearch.com/v1`
   - `HERMES_MODEL=Hermes-4-70B` — confirm the exact model id available in your Portal (e.g. `Hermes-4-405B` for higher quality, slower).
> The hosted endpoint is used because a cloud Modal container can't reach the
> Hermes Agent desktop server (`localhost:8642`). To use that instead, expose it
> publicly and set `HERMES_BASE_URL` to its URL.

## 3. PayPal Sandbox — the money
1. At <https://developer.paypal.com> create a sandbox **REST app** → copy its **Client ID** + **Secret** → `.env` (`PAYPAL_CLIENT_ID`, `PAYPAL_SECRET`; `PAYPAL_BASE=https://api-m.sandbox.paypal.com`).
2. Create sandbox **business** (merchant) + **personal** (buyer) test accounts.
3. Log the demo device into the **buyer** account so a sale can be approved on cue. The flow is clearly labelled sandbox — full real flow, no real charge.

## 4. Modal — autonomy + hosting
1. `pip install modal` then `modal token new`.
2. Create the runtime Secret from your `.env`:
   ```bash
   modal secret create incline-secrets --from-dotenv .env
   ```
3. First deploy to learn your URL, then wire it back:
   ```bash
   modal deploy app.py          # copy the printed web URL
   # set BASE_URL=<that url> in .env, update the secret, then:
   modal deploy app.py          # redeploy so links + PayPal returns are absolute
   ```
4. `modal deploy` keeps the scheduled loop alive after you leave. `modal run app.py` does a one-off dry run (seed + one pass).

## 5. Reddit — listening (no setup)
Uses the plain Reddit JSON path — no app, no auth. Configure `REDDIT_SUBREDDITS`
and `REDDIT_USER_AGENT` in `.env`. Optionally seed real example posts in `seeds.json`.

---

## Bring-up order
1. Supabase: create project → run `schema.sql` → copy 3 keys.
2. Fill `.env` (Supabase + Hermes + PayPal).
3. `modal token new` → `modal secret create incline-secrets --from-dotenv .env`.
4. `modal deploy app.py` → set `BASE_URL` → update secret → `modal deploy app.py`.
5. Paste `SUPABASE_URL` + anon key into `dashboard/index.html`; open it.
6. Log the demo device into the PayPal buyer sandbox account.
7. `modal run app.py` (or `POST {BASE_URL}/seed`) → watch the dashboard tick.

Controls: kill switch `POST {BASE_URL}/kill` (resume `/resume`). Replies stay in
`REPLY_MODE=draft` (drafted + shown, never posted) unless you flip it.
