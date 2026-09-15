-- Supabase schema for the Zey Batch SQLite mirror.
-- Run once in Supabase SQL Editor. The server Secret key is not a DDL key.
-- All tables are RLS-enabled; only trusted server-side credentials may write.

create table if not exists public.customers (
  customer_id bigint primary key, vagaro_user_id text unique, mobile text not null,
  first_name text, last_name text, email text, birthdate text, gender text,
  address text, city text, state text, zip text, apt_suite text,
  customer_since text, last_visit text, membership text, referred_by text,
  online_booking text, tags text, communication_preference text default 'sms',
  sms_opt_out integer default 0, opt_out_date text, email_opt_out integer default 0,
  active integer default 1, acquisition text, bank_name_number text, cdn_url text,
  country_id text, custom_fields_groups text, day_phone text,
  email_failed_reason text, email_format text, general_tag text,
  is_valid_email integer, is_valid_text integer, night_phone text,
  no_of_booking integer, no_of_class_booked integer, no_of_class_check_ins integer,
  no_show_cancel integer, photo text, service_providers text,
  street_address text, street_no text, text_failed_reason text,
  total_amount_paid double precision, total_points_accumulated double precision,
  ucc_no text, ucc_type text, enc_user_id text, raw_json text,
  created_at text default now()::text, updated_at text default now()::text
);

create table if not exists public.services (
  service_id bigint primary key, customer_id bigint, employee_name text,
  service_name text, service_date text, duration_min integer,
  amount_paid double precision, notes text, vagaro_appt_id text unique,
  created_at text default now()::text
);

create table if not exists public.employees (
  employee_id bigint primary key, vagaro_emp_id text unique, name text,
  role text, phone text, email text, active integer default 1,
  schedule_json text, created_at text default now()::text,
  updated_at text default now()::text
);

create table if not exists public.transactions (
  transaction_id bigint primary key, vagaro_transaction_id text unique not null,
  customer_id bigint, customer_name text, employee_name text,
  transaction_date text, transaction_type text, payment_method text,
  subtotal double precision, tax double precision, tip double precision,
  discount double precision, total_amount double precision, status text,
  notes text, raw_json text, created_at text default now()::text
);

create table if not exists public.sms_history (
  sms_id bigint primary key, customer_id bigint, campaign_type text,
  message_text text, sent_at text default now()::text, status text,
  twilio_sid text, error_message text, campaign_row integer,
  created_at text default now()::text
);

create table if not exists public.email_history (
  email_id bigint primary key, customer_id bigint, campaign_type text,
  subject text, body text, sent_at text default now()::text, status text,
  error_message text, campaign_row integer, created_at text default now()::text
);

create table if not exists public.campaigns (
  campaign_id bigint primary key, text_prompt text,
  character_limit integer default 160, campaign_type text default 'Campaign',
  filter_last_visit_days integer, filter_last_sms_days integer,
  rank integer default 999, process_date text, process_status text,
  channels text default 'sms', email_subject text, email_html text,
  active integer default 1, created_at text default now()::text,
  updated_at text default now()::text
);

-- Safe additions for projects that already ran the first version of this file.
alter table public.email_history add column if not exists campaign_row integer;
alter table public.campaigns add column if not exists channels text default 'sms';
alter table public.campaigns add column if not exists email_subject text;
alter table public.campaigns add column if not exists email_html text;

create table if not exists public.sync_log (
  log_id bigint primary key, sync_date text default now()::text, source text,
  records_fetched integer, records_inserted integer, records_updated integer,
  records_deactivated integer, errors text, duration_sec double precision
);

create table if not exists public.webhook_events (
  event_id text primary key, event_type text not null, action text,
  event_created_at text, payload_json text not null,
  received_at text default now()::text, processed_at text,
  process_status text not null default 'received', error_message text
);

do $$
declare table_name text;
begin
  foreach table_name in array array['customers','services','employees','transactions',
    'sms_history','email_history','campaigns','sync_log','webhook_events'] loop
    execute format('alter table public.%I enable row level security', table_name);
  end loop;
end $$;

-- Safety fields added after the 2026-09-14 accidental live-send incident.
-- approved must be explicitly 1 for ANY campaign to be eligible to run,
-- regardless of campaign_type (closes the 'Campaign type always pending'
-- gap). test_recipients, when non-empty, restricts a run to ONLY those
-- comma-separated phone numbers / emails, overriding normal customer
-- filtering entirely.
alter table public.campaigns add column if not exists approved integer default 0;
alter table public.campaigns add column if not exists test_recipients text;

-- Employee name resolution fix (2026-09-15): webhooks send Vagaro's
-- encrypted staff id, which had no matching column before.
alter table public.employees add column if not exists enc_emp_id text;

-- Auto-touch updated_at on every UPDATE (2026-09-15): without this, a
-- manual edit in the Table Editor leaves updated_at unchanged, and
-- pull_from_supabase.py's last-write-wins comparison then incorrectly
-- treats the fresh edit as stale and skips it.
create or replace function public.touch_updated_at()
returns trigger as $$
begin
  new.updated_at = now()::text;
  return new;
end;
$$ language plpgsql;

do $$
declare t text;
begin
  foreach t in array array['customers','campaigns','employees'] loop
    execute format('drop trigger if exists trg_touch_updated_at on public.%I', t);
    execute format(
      'create trigger trg_touch_updated_at before update on public.%I
       for each row execute function public.touch_updated_at()', t
    );
  end loop;
end $$;
