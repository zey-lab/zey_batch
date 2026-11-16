-- 2026-11-16: the month-end revenue forecast as it stood each morning
-- (src/sms_campaign/forecast_models.py, written by
-- scripts/forecast_snapshots.py). The model takes seconds to fit, so it runs
-- once per Chicago date and the panel and the daily report read the result.
-- days_json holds the expected revenue of every day from as_of to the end of
-- its month, so the panel can combine it with today's takings as they come in.
create table if not exists public.forecast_snapshots (
  as_of date primary key,
  model text not null,
  actual_before double precision not null,
  days_json text not null,
  created_at timestamptz not null default now()
);

-- Like every other table (see 006): RLS on, no policies; the app connects
-- as postgres, which bypasses RLS.
alter table public.forecast_snapshots enable row level security;
revoke all on public.forecast_snapshots from anon, authenticated;
