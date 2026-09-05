#!/usr/bin/env node
/**
 * Daily Zey Brow full data sync orchestrator.
 * Runs as a script-only Hermes cron job (no LLM tokens).
 *
 * Pipeline:
 * 1. Check Vagaro session health
 * 2. Extract customers, services, employees, transactions from Vagaro
 * 3. Sync all data into SQLite
 * 4. Mirror all tables to Google Sheets
 * 5. Backup SQLite to Google Drive
 * 6. Clean local incoming files
 * 7. Report results to stdout
 */

import { execSync } from 'node:child_process';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const ROOT = '/opt/data/zey_batch';
const SCRIPTS = {
  sessionCheck: `${ROOT}/scripts/check_vagaro_session.mjs`,
  extractCustomers: `${ROOT}/scripts/extract_vagaro_customers.mjs`,
  extractAppointments: `${ROOT}/scripts/extract_vagaro_appointments.mjs`,
  extractEmployees: `${ROOT}/scripts/extract_vagaro_employees.mjs`,
  extractTransactions: `${ROOT}/scripts/extract_vagaro_transactions.mjs`,
  syncCustomers: `${ROOT}/scripts/sync_to_database.py`,
  syncServices: `${ROOT}/scripts/sync_services.py`,
  syncEmployees: `${ROOT}/scripts/sync_employees.py`,
  syncTransactions: `${ROOT}/scripts/sync_transactions.py`,
  syncCampaigns: `${ROOT}/scripts/sync_campaigns.py`,
  mirror: `${ROOT}/scripts/mirror_to_sheets.py`,
  backup: `${ROOT}/scripts/backup_to_drive.py`,
};

function run(cmd) {
  try { return execSync(cmd, { encoding: 'utf-8', timeout: 600_000, cwd: ROOT }); }
  catch (e) { return JSON.stringify({ error: e.message }); }
}

function parseJson(raw) {
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

function halt(report, step) {
  report.status = 'error';
  report.failedStep = step;
  process.stdout.write(JSON.stringify(report) + '\n');
  process.exit(1);
}

// No timezone gate — cron schedule handles timing (0 23,0 * * * UTC = 18:00 Central)

const report = { startedAt: new Date().toISOString(), steps: {} };

// Step 1: Session health
const sessionRaw = run(`node ${SCRIPTS.sessionCheck}`);
report.steps.session = parseJson(sessionRaw);
if (!report.steps.session || report.steps.session.status !== 'ok' || !report.steps.session.authenticated) {
  report.steps.session = report.steps.session || {};
  report.steps.session.message = 'Vagaro session expired. Re-login required.';
  report.status = 'error';
  process.stdout.write(JSON.stringify(report) + '\n');
  process.exit(1);
}

// Step 2a: Extract customers
report.steps.extractCustomers = parseJson(run(`node ${SCRIPTS.extractCustomers}`));
if (!report.steps.extractCustomers || report.steps.extractCustomers.error) halt(report, 'extractCustomers');

// Step 2b: Extract services/appointments
report.steps.extractAppointments = parseJson(run(`node ${SCRIPTS.extractAppointments}`));
if (!report.steps.extractAppointments || report.steps.extractAppointments.error) halt(report, 'extractAppointments');

// Step 2c: Extract employees
report.steps.extractEmployees = parseJson(run(`node ${SCRIPTS.extractEmployees}`));
if (!report.steps.extractEmployees || report.steps.extractEmployees.error) halt(report, 'extractEmployees');

// Step 2d: Extract transactions
report.steps.extractTransactions = parseJson(run(`node ${SCRIPTS.extractTransactions}`));
if (!report.steps.extractTransactions || report.steps.extractTransactions.error) halt(report, 'extractTransactions');

// Step 3a: Sync customers to SQLite
report.steps.syncCustomers = parseJson(run(`uv run python ${SCRIPTS.syncCustomers}`));
if (!report.steps.syncCustomers || report.steps.syncCustomers.error) halt(report, 'syncCustomers');

// Step 3b: Sync services to SQLite
report.steps.syncServices = parseJson(run(`uv run python ${SCRIPTS.syncServices}`));
if (!report.steps.syncServices || report.steps.syncServices.error) halt(report, 'syncServices');

// Step 3c: Sync employees to SQLite
report.steps.syncEmployees = parseJson(run(`uv run python ${SCRIPTS.syncEmployees}`));
if (!report.steps.syncEmployees || report.steps.syncEmployees.error) halt(report, 'syncEmployees');

// Step 3d: Sync transactions to SQLite
report.steps.syncTransactions = parseJson(run(`uv run python ${SCRIPTS.syncTransactions}`));
if (!report.steps.syncTransactions || report.steps.syncTransactions.error) halt(report, 'syncTransactions');

// Import the existing local campaign definitions before mirroring. A missing
// source is reported as "skipped" so Vagaro data sync is not lost; once
// campaigns.xlsx (or CAMPAIGN_CONFIG_PATH) is supplied, the next run imports
// it idempotently and populates the Campaigns tab.
report.steps.syncCampaigns = parseJson(run(`uv run python ${SCRIPTS.syncCampaigns}`));
if (!report.steps.syncCampaigns || report.steps.syncCampaigns.error) halt(report, 'syncCampaigns');

// Step 4: Mirror all tables to Google Sheets — gated on successful sync above
report.steps.mirror = parseJson(run(`uv run python ${SCRIPTS.mirror}`));
if (!report.steps.mirror || report.steps.mirror.error) halt(report, 'mirror');

// Step 5: Backup SQLite to Google Drive
report.steps.backup = parseJson(run(`uv run python ${SCRIPTS.backup}`));

// Step 6: Get final stats
report.steps.stats = parseJson(run(`uv run python -c "
from sms_campaign.data_store import ZeyDataStore
from pathlib import Path
import json
store = ZeyDataStore(Path('${ROOT}/data/customer_master.sqlite3'))
print(json.dumps(store.get_stats()))
"`));

report.status = 'ok';
report.completedAt = new Date().toISOString();
process.stdout.write(JSON.stringify(report) + '\n');
