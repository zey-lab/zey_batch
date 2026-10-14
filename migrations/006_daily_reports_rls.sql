-- 2026-10-14: Supabase Security Advisor flagged public.daily_reports
-- (rls_disabled_in_public): 004 created it without the row level security
-- that 001 turns on for every other table, so anyone with the project URL
-- and the public anon key could read, change or delete it via PostgREST.
-- No policies, like the other tables: the app connects as postgres, which
-- bypasses RLS.
alter table public.daily_reports enable row level security;

-- Defense in depth: nothing uses PostgREST, so the anon and authenticated
-- roles get no table access at all, now or for tables created later, even
-- if a future migration forgets RLS again. service_role is untouched.
revoke all on all tables in schema public from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke all on tables from anon, authenticated;
