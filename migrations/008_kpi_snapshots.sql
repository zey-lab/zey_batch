-- 2026-11-16: one value per Chicago date and key, kept so the panel can show
-- how a number moves over time when the data can't be replayed later:
-- whether last week's customers hold a future booking (rebook.*), and how
-- many visits are booked for each of the next 7 days (booked:<date>), which
-- Vagaro doesn't keep and the panel can only see as of today.
create table if not exists public.kpi_snapshots (
  snap_date date not null,
  key text not null,
  value double precision not null,
  created_at timestamptz not null default now(),
  primary key (snap_date, key)
);

-- Like every other table (see 006): RLS on, no policies; the app connects
-- as postgres, which bypasses RLS.
alter table public.kpi_snapshots enable row level security;
revoke all on public.kpi_snapshots from anon, authenticated;
