-- ============================================================================
-- Incline — Supabase schema (S-E5-1). Run this FIRST in the Supabase SQL editor.
-- Source of truth: architecture.md §5. One migration, run once. No RLS (hackathon).
-- Caps harmonized to GBP / £15 (implementation-readiness §2.4).
-- ============================================================================

-- ── signals: every demand-intent post heard ─────────────────────────────
create table if not exists signals (
  id            uuid primary key default gen_random_uuid(),
  source        text not null,              -- 'reddit' | 'x'
  source_id     text not null,              -- platform post/comment id (dedupe key)
  author        text,
  text          text not null,
  url           text,
  is_primed     boolean not null default false,
  intent_match  boolean not null default true,   -- passed demand-intent filter
  fetched_at    timestamptz not null default now(),
  unique (source, source_id)               -- idempotent ingest
);

-- ── opportunities: the "ear" verdict + scores + triage state ─────────────
create table if not exists opportunities (
  id            uuid primary key default gen_random_uuid(),
  signal_id     uuid not null references signals(id),
  verdict       text not null,             -- POINT_FREE | BUILD_SIMPLE | UNDERCUT | REAL_GAP
  rationale     text,                      -- one-line human-readable reason
  free_pointer  text,                      -- for POINT_FREE: the existing free tool
  incumbent     text,                      -- for UNDERCUT: named rival + price
  pain          int,                       -- 1..10
  reach         int,                       -- 1..10
  pain_reach    int generated always as (pain * reach) stored,
  urgency       int,                       -- 1..10
  payability    int,                       -- 1..10 (folds verdict class)
  triage_score  int generated always as (urgency * payability) stored,
  status        text not null default 'scored',  -- scored|triaged|point_free|queued|dropped|built
  drop_reason   text,
  created_at    timestamptz not null default now()
);

-- ── tools: what got built/deployed for an opportunity ────────────────────
create table if not exists tools (
  id             uuid primary key default gen_random_uuid(),
  opportunity_id uuid not null references opportunities(id),
  signal_id      uuid not null references signals(id),
  archetype      text not null,            -- e.g. 'text_transformer' | 'calculator'
  html           text not null,            -- the full single-file tool (served verbatim)
  url            text,                     -- https://<modal>/t/<id>
  price          numeric(10,2) not null default 9.00,
  currency       text not null default 'GBP',
  model          text not null default 'one_time',  -- one_time | subscription
  used_fallback  boolean not null default false,
  generated_at   timestamptz not null default now()
);

-- ── sales: the revenue ledger (the money) ────────────────────────────────
create table if not exists sales (
  id             uuid primary key default gen_random_uuid(),
  tool_id        uuid references tools(id),
  signal_id      uuid references signals(id),
  paypal_order_id text not null unique,    -- idempotency: 1 capture = 1 row
  amount         numeric(10,2) not null,
  currency       text not null default 'GBP',
  model          text not null default 'one_time',
  source         text,                     -- 'reddit' | 'x' (origin channel)
  paid_at        timestamptz not null default now()
);

-- ── guardrail_limits: the visible hard caps (single config row) ──────────
create table if not exists guardrail_limits (
  id                 int primary key default 1,
  max_spend          numeric(10,2) not null default 15.00,  -- total £ Incline may spend
  spend_used         numeric(10,2) not null default 0.00,
  max_posts_per_hour int not null default 10,
  max_price          numeric(10,2) not null default 15.00,  -- ceiling per tool (£)
  paused             boolean not null default false,        -- kill switch
  updated_at         timestamptz not null default now(),
  check (id = 1)                                            -- enforce single row
);
insert into guardrail_limits (id) values (1) on conflict do nothing;

-- ── activity_log: the audit trail + live feed source ─────────────────────
create table if not exists activity_log (
  id          uuid primary key default gen_random_uuid(),
  signal_id   uuid references signals(id),
  stage       text not null,             -- heard|qualified|triaged|built|deployed|replied|paid|blocked|escalation
  detail      text,                      -- human-readable line (verdict, url, reason…)
  meta        jsonb,                     -- structured extras (cost, price, used_fallback…)
  created_at  timestamptz not null default now()
);

-- Enable Realtime on the two tables the dashboard subscribes to:
-- (wrapped so re-running the migration does not error if already added)
do $$
begin
  begin
    alter publication supabase_realtime add table sales;
  exception when duplicate_object then null;
  end;
  begin
    alter publication supabase_realtime add table activity_log;
  exception when duplicate_object then null;
  end;
end $$;

-- Convenience view for the counter (one query):
create or replace view revenue_summary as
  select coalesce(sum(amount),0) as total_revenue,
         count(*)               as sale_count,
         max(paid_at)           as last_sale_at
  from sales;
