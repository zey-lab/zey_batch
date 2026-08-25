#!/usr/bin/env node
/**
 * Daily Zey Brow customer data sync orchestrator.
 * Runs as a script-only Hermes cron job (no LLM tokens).
 *
 * Pipeline:
 * 1. Check Vagaro session health
 * 2. Extract customers from Vagaro
 * 3. Sync into SQLite (customers + services + employees)
 * 4. Mirror all tables to Google Sheets
 * 5. Report results to stdout (delivered to Discord)
 */

import { execSync } from 'node:child_process';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const ROOT = '/opt/data/zey_batch';
const EXTRACT = `${ROOT}/scripts/extract_vagaro_customers.mjs`;
const SESSION_CHECK = `${ROOT}/scripts/check_vagaro_session.mjs`;
const SYNC_PY = `${ROOT}/scripts/sync_to_database.py`;
const MIRROR_PY = `${ROOT}/scripts/mirror_to_sheets.py`;

function run(cmd) {
  try { return execSync(cmd, { encoding: 'utf-8', timeout: 600_000, cwd: ROOT }); }
  catch { return null; }
}

// Timezone gate: only run at 18:00-18:05 America/Chicago
const now = new Date();
const chicagoTime = new Date(now.toLocaleString('en-US', { timeZone: 'America/Chicago' }));
if (chicagoTime.getHours() !== 18 || chicagoTime.getMinutes() > 5) process.exit(0);

const report = { startedAt: new Date().toISOString(), steps: {} };

// Step 1: Session health
const sessionCheck = run(`node ${SESSION_CHECK}`);
if (!sessionCheck) { report.steps.session = { status: 'error' }; process.stdout.write(JSON.stringify(report) + '\n'); process.exit(1); }
report.steps.session = JSON.parse(sessionCheck);
if (report.steps.session.status !== 'ok' || !report.steps.session.authenticated) {
  report.steps.session.message = 'Vagaro session expired. Re-login required.';
  process.stdout.write(JSON.stringify(report) + '\n'); process.exit(1);
}

// Step 2: Extract customers
const exportResult = run(`node ${EXTRACT}`);
if (!exportResult) { report.steps.extract = { status: 'error' }; process.stdout.write(JSON.stringify(report) + '\n'); process.exit(1); }
report.steps.extract = JSON.parse(exportResult);

// Step 3: Sync to SQLite
const syncResult = run(`uv run python ${SYNC_PY}`);
if (!syncResult) { report.steps.sync = { status: 'error' }; process.stdout.write(JSON.stringify(report) + '\n'); process.exit(1); }
report.steps.sync = JSON.parse(syncResult);

// Step 4: Mirror to Google Sheets
const mirrorResult = run(`uv run python ${MIRROR_PY}`);
if (!mirrorResult) { report.steps.mirror = { status: 'error' }; process.stdout.write(JSON.stringify(report) + '\n'); process.exit(1); }
report.steps.mirror = JSON.parse(mirrorResult);

report.status = 'ok';
report.completedAt = new Date().toISOString();
process.stdout.write(JSON.stringify(report) + '\n');
