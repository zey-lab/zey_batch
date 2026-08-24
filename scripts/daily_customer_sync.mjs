#!/usr/bin/env node
/**
 * Orchestrator for the daily Vagaro customer sync pipeline.
 * Connects to the running authenticated Chromium daemon, exports
 * the full Customer Report via UI pagination, then shells out to
 * Python for SQLite sync and Google Sheet mirror.
 *
 * Designed as a Hermes script-only cron job (no LLM, no tokens).
 * PII never leaves this script except to data/incoming/*.json (gitignored).
 */

import { execSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const ROOT = '/opt/data/zey_batch';
const EXPORT_SCRIPT = `${ROOT}/scripts/export_vagaro_customers.mjs`;
const SYNC_SCRIPT = `${ROOT}/scripts/sync_vagaro_export_to_db.py`;
const MIRROR_SCRIPT = `${ROOT}/scripts/mirror_customer_master_to_sheet.py`;
const SESSION_CHECK = `${ROOT}/scripts/check_vagaro_session.mjs`;

function run(cmd, opts = {}) {
  try {
    return execSync(cmd, { encoding: 'utf-8', timeout: 600_000, ...opts });
  } catch (error) {
    return null;
  }
}

// Step 0: Timezone gate — only run at 18:00 America/Chicago
const now = new Date();
const chicagoTime = new Date(now.toLocaleString('en-US', { timeZone: 'America/Chicago' }));
const hour = chicagoTime.getHours();
const minute = chicagoTime.getMinutes();
if (hour !== 18 || minute > 5) {
  // Silent exit: not our window
  process.exit(0);
}

const report = { startedAt: new Date().toISOString(), steps: {} };

// Step 1: Check Vagaro session
const sessionCheck = run(`node ${SESSION_CHECK}`);
if (!sessionCheck) {
  report.steps.session = { status: 'error', message: 'Session check script failed.' };
  process.stdout.write(JSON.stringify(report) + '\n');
  process.exit(1);
}
const session = JSON.parse(sessionCheck);
report.steps.session = session;
if (session.status !== 'ok' || !session.authenticated) {
  report.steps.session.message = 'Vagaro session is not authenticated. Re-login required.';
  process.stdout.write(JSON.stringify(report) + '\n');
  process.exit(1);
}

// Step 2: Export from Vagaro
const exportResult = run(`node ${EXPORT_SCRIPT}`);
if (!exportResult) {
  report.steps.export = { status: 'error', message: 'Vagaro export script failed.' };
  process.stdout.write(JSON.stringify(report) + '\n');
  process.exit(1);
}
report.steps.export = JSON.parse(exportResult);

// Step 3: Sync to SQLite
const syncResult = run(`uv run python ${SYNC_SCRIPT}`, { cwd: ROOT });
if (!syncResult) {
  report.steps.sync = { status: 'error', message: 'SQLite sync script failed.' };
  process.stdout.write(JSON.stringify(report) + '\n');
  process.exit(1);
}
report.steps.sync = JSON.parse(syncResult);

// Step 4: Mirror to Google Sheet
const mirrorResult = run(`uv run python ${MIRROR_SCRIPT}`, { cwd: ROOT });
if (!mirrorResult) {
  report.steps.mirror = { status: 'error', message: 'Google Sheet mirror script failed.' };
  process.stdout.write(JSON.stringify(report) + '\n');
  process.exit(1);
}
report.steps.mirror = JSON.parse(mirrorResult);

report.status = 'ok';
report.completedAt = new Date().toISOString();
process.stdout.write(JSON.stringify(report) + '\n');
