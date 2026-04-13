-- =============================================================================
-- AgentIC Supabase Migration: Plan Billing + Payments
-- =============================================================================
-- Run this in your Supabase SQL editor (Database → SQL Editor → New query)
-- Or via: supabase db push (if using Supabase CLI)
--
-- What this migration does:
-- 1. Adds plan_type, build_limit, razorpay_order_id to profiles table
-- 2. Creates payments table for billing records
-- 3. Sets up Row Level Security (RLS) policies
-- =============================================================================

-- ── 1. Add billing columns to profiles ──────────────────────────────────────────
ALTER TABLE public.profiles
  ADD COLUMN IF NOT EXISTS plan_type TEXT DEFAULT 'byok',
  ADD COLUMN IF NOT EXISTS build_limit INTEGER,
  ADD COLUMN IF NOT EXISTS razorpay_order_id TEXT;

COMMENT ON COLUMN public.profiles.plan_type IS 'How the user pays: agentic_paid (uses server model) or byok (user provides own keys)';
COMMENT ON COLUMN public.profiles.build_limit IS 'Max successful builds. NULL = unlimited. Set to 10 for starter plan.';
COMMENT ON COLUMN public.profiles.razorpay_order_id IS 'Most recent Razorpay order ID for this user';

-- ── 2. Create payments table ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.payments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  razorpay_order_id TEXT,
  razorpay_payment_id TEXT,
  razorpay_signature TEXT,
  plan TEXT NOT NULL CHECK (plan IN ('starter', 'unlimited')),
  amount_paise BIGINT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'captured', 'failed', 'refunded')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Index for fast lookups by order_id
CREATE INDEX IF NOT EXISTS idx_payments_order_id ON public.payments(razorpay_order_id);
CREATE INDEX IF NOT EXISTS idx_payments_user_id ON public.payments(user_id);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS set_payments_updated_at ON public.payments;
CREATE TRIGGER set_payments_updated_at
  BEFORE UPDATE ON public.payments
  FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- ── 3. Row Level Security ───────────────────────────────────────────────────────
ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.payments ENABLE ROW LEVEL SECURITY;

-- Users can read their own profile
DROP POLICY IF EXISTS "Users can read own profile" ON public.profiles;
CREATE POLICY "Users can read own profile"
  ON public.profiles FOR SELECT
  USING (auth.uid() = id);

-- Users can update their own profile (but not plan_type/build_limit directly)
DROP POLICY IF EXISTS "Users can update own profile" ON public.profiles;
CREATE POLICY "Users can update own profile"
  ON public.profiles FOR UPDATE
  USING (auth.uid() = id);

-- Users can only read their own payments
DROP POLICY IF EXISTS "Users can read own payments" ON public.payments;
CREATE POLICY "Users can read own payments"
  ON public.payments FOR SELECT
  USING (auth.uid() = user_id);

-- ── 4. Fix existing profiles ───────────────────────────────────────────────────
-- Set plan_type to 'byok' for all existing users who don't have llm_api_key set
UPDATE public.profiles
SET plan_type = 'byok'
WHERE plan_type IS NULL OR plan_type = '';

-- Set plan_type to 'byok' for users who have BYOK keys configured
UPDATE public.profiles
SET plan_type = 'byok'
WHERE llm_api_key IS NOT NULL AND llm_api_key != ''
  AND (plan_type IS NULL OR plan_type = '');

-- ── 5. Useful views ────────────────────────────────────────────────────────────
-- View: user_billing_summary
CREATE OR REPLACE VIEW public.user_billing_summary AS
SELECT
  p.id AS user_id,
  p.email,
  p.plan_type,
  p.plan AS razorpay_plan,
  p.build_limit,
  p.successful_builds,
  COALESCE(p.build_limit - p.successful_builds, 999999) AS builds_remaining,
  p.razorpay_order_id,
  (SELECT status FROM public.payments WHERE user_id = p.id ORDER BY created_at DESC LIMIT 1) AS last_payment_status,
  (SELECT created_at FROM public.payments WHERE user_id = p.id ORDER BY created_at DESC LIMIT 1) AS last_payment_at
FROM public.profiles p
JOIN auth.users u ON u.id = p.id;

-- Grant access to the view
GRANT SELECT ON public.user_billing_summary TO authenticated;
GRANT SELECT ON public.user_billing_summary TO anon;

-- ── 6. RPC function for incrementing successful builds ────────────────────────
-- (This should already exist from a previous migration, but we recreate it safely)
CREATE OR REPLACE FUNCTION public.increment_successful_builds(uid UUID)
RETURNS void AS $$
BEGIN
  UPDATE public.profiles
  SET successful_builds = COALESCE(successful_builds, 0) + 1
  WHERE id = uid;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Make it callable by the API service role
GRANT EXECUTE ON FUNCTION public.increment_successful_builds TO service_role;
GRANT EXECUTE ON FUNCTION public.increment_successful_builds TO anon;

-- ── 7. Verify ──────────────────────────────────────────────────────────────────
SELECT 'Migration complete!' AS status;
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_name = 'profiles' AND table_schema = 'public'
AND column_name IN ('plan_type', 'build_limit', 'razorpay_order_id');
