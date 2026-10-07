-- 2026-10-07: daily LLM-written "Durum Değerlendirmesi ve Aksiyon Planı"
-- for the live panel (scripts/daily_report.py). One row per Chicago date.
-- facts_json is the exact fact list the model was given, so every published
-- sentence can be traced back to the numbers it was checked against.
create table if not exists public.daily_reports (
  report_date date primary key,
  generated_at timestamptz not null default now(),
  status text not null check (status in ('published', 'rejected')),
  model text,
  attempts integer not null default 1,
  facts_json text not null,
  report_json text,
  checks_json text
);
