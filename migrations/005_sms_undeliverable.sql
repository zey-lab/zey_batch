-- 2026-10-07: why Twilio can't deliver SMS to a customer's number (invalid,
-- landline, inactive, region without SMS permission, or the last 3 sends
-- failed), set by scripts/sync_sms_status.py. Campaigns skip these customers
-- instead of retrying every day; the panel lists them so the number can be
-- fixed in Vagaro, and a changed number from the customer webhook clears it.
-- Separate from is_valid_text, which the Vagaro report import overwrites.
alter table public.customers add column if not exists sms_undeliverable text;
