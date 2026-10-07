-- 2026-10-07: keep Vagaro's appointment lifecycle on each service row.
-- Appointment webhooks carry bookingStatus (Confirmed, Accepted, Show,
-- Service Completed, Cancel; 'deleted' events carry Deleted) but it was
-- never stored, so cancelled and deleted appointments looked like live
-- bookings and reports could not tell a completed visit from a pending one.
alter table public.services add column if not exists booking_status text;
