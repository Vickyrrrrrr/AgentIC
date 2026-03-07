-- ============================================================
-- AgentIC Auth & Billing Schema — Supabase (PostgreSQL)
-- ============================================================
-- Run this in Supabase SQL Editor (Dashboard → SQL Editor → New query)

-- Enable Row Level Security on all tables
-- Enable the pgcrypto extension for encryption
create extension if not exists pgcrypto;

-- ─── 1. User Profiles ──────────────────────────────────────
-- Links to Supabase auth.users via id (UUID)
create table if not exists public.profiles (
    id            uuid primary key references auth.users(id) on delete cascade,
    email         text not null,
    full_name     text,
    plan          text not null default 'free'
                    check (plan in ('free', 'starter', 'pro', 'byok')),
    successful_builds  int not null default 0,
    llm_api_key   text,                       -- encrypted via pgp_sym_encrypt
    razorpay_customer_id text,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);

alter table public.profiles enable row level security;

-- Users can read/update only their own profile
create policy "Users read own profile"
    on public.profiles for select
    using (auth.uid() = id);

create policy "Users update own profile"
    on public.profiles for update
    using (auth.uid() = id);

-- ─── 2. Build History ──────────────────────────────────────
create table if not exists public.builds (
    id            uuid primary key default gen_random_uuid(),
    user_id       uuid not null references public.profiles(id) on delete cascade,
    job_id        text not null,                  -- maps to backend JOB_STORE key
    design_name   text not null,
    status        text not null default 'queued'
                    check (status in ('queued', 'running', 'done', 'failed', 'cancelled')),
    created_at    timestamptz not null default now(),
    finished_at   timestamptz
);

alter table public.builds enable row level security;

create policy "Users read own builds"
    on public.builds for select
    using (auth.uid() = user_id);

create policy "Service role inserts builds"
    on public.builds for insert
    with check (true);  -- insert via service-role key from backend

create policy "Service role updates builds"
    on public.builds for update
    using (true);

-- ─── 3. Payment Events ────────────────────────────────────
create table if not exists public.payments (
    id                uuid primary key default gen_random_uuid(),
    user_id           uuid not null references public.profiles(id) on delete cascade,
    razorpay_order_id    text,
    razorpay_payment_id  text,
    razorpay_signature   text,
    amount_paise      int not null,               -- amount in paise (₹1 = 100 paise)
    plan              text not null
                        check (plan in ('starter', 'pro', 'byok')),
    status            text not null default 'pending'
                        check (status in ('pending', 'captured', 'failed', 'refunded')),
    created_at        timestamptz not null default now()
);

alter table public.payments enable row level security;

create policy "Users view own payments"
    on public.payments for select
    using (auth.uid() = user_id);

-- ─── 4. Plan Limits (reference table) ─────────────────────
create table if not exists public.plan_limits (
    plan              text primary key
                        check (plan in ('free', 'starter', 'pro', 'byok')),
    max_builds        int,            -- NULL = unlimited
    price_paise       int not null default 0,
    label             text not null
);

insert into public.plan_limits (plan, max_builds, price_paise, label) values
    ('free',    2,    0,        'Free Tier — 2 builds'),
    ('starter', 25,   49900,    'Starter — 25 builds (₹499)'),
    ('pro',     null, 149900,   'Pro — Unlimited builds (₹1,499)'),
    ('byok',    null, 0,        'BYOK — Bring Your Own Key')
on conflict (plan) do nothing;

-- ─── 5. Auto-create profile on signup ──────────────────────
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer set search_path = public
as $$
begin
    insert into public.profiles (id, email, full_name)
    values (
        new.id,
        new.email,
        coalesce(new.raw_user_meta_data->>'full_name', split_part(new.email, '@', 1))
    );
    return new;
end;
$$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
    after insert on auth.users
    for each row execute procedure public.handle_new_user();

-- ─── 6. Helper: increment builds ──────────────────────────
create or replace function public.increment_successful_builds(uid uuid)
returns void
language plpgsql
security definer
as $$
begin
    update public.profiles
    set successful_builds = successful_builds + 1,
        updated_at = now()
    where id = uid;
end;
$$;
