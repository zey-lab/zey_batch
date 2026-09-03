#!/usr/bin/env node
/**
 * Extract Vagaro Transaction List report via authenticated Chromium daemon.
 * Navigates to Reports > Sales > Transaction List and intercepts the API response.
 *
 * REPORT_URL/REPORT_ENDPOINT are env-overridable for future Vagaro changes.
 */

import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '/opt/data/browser-worker/node_modules/playwright');

const CDP_URL = process.env.VAGARO_CDP_URL || 'http://127.0.0.1:9222';
const REPORT_URL = process.env.VAGARO_TRANSACTIONS_REPORT_URL || 'https://us04.vagaro.com/merchants/reports/sales/transactionlist';
const OUTPUT_DIR = process.env.VAGARO_EXPORT_DIR || path.resolve('data/incoming');
const OUTPUT_PATH = path.join(OUTPUT_DIR, 'vagaro_transactions_latest.json');
const REPORT_ENDPOINT = process.env.VAGARO_TRANSACTIONS_REPORT_ENDPOINT || '/merchants/reports/sales/gettransactionlist';

function nextReportResponse(cdp) {
  return new Promise((resolve, reject) => {
    let targetRequestId;
    const cleanup = () => {
      clearTimeout(timeout);
      cdp.off('Network.responseReceived', responseHandler);
      cdp.off('Network.loadingFinished', finishedHandler);
    };
    const timeout = setTimeout(() => { cleanup(); reject(new Error('Timeout waiting for transactions response.')); }, 60_000);
    const responseHandler = (event) => {
      if (event.response.status === 200 && event.response.url.includes(REPORT_ENDPOINT)) {
        targetRequestId = event.requestId;
      }
    };
    const finishedHandler = async (event) => {
      if (event.requestId !== targetRequestId) return;
      try {
        const raw = await cdp.send('Network.getResponseBody', { requestId: event.requestId });
        cleanup();
        resolve(JSON.parse(raw.body));
      } catch (error) { cleanup(); reject(error); }
    };
    cdp.on('Network.responseReceived', responseHandler);
    cdp.on('Network.loadingFinished', finishedHandler);
  });
}

function reportRows(payload) {
  const data = payload?.Data;
  if (Array.isArray(data)) return { rows: data, total: data.length };
  if (Array.isArray(data?.TransactionList)) {
    return { rows: data.TransactionList, total: Number(data.TotalItems ?? data.TransactionList.length) };
  }
  throw new Error('Invalid transactions report response.');
}

async function main() {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true, mode: 0o700 });
  const browser = await chromium.connectOverCDP(CDP_URL);
  const page = browser.contexts().flatMap(c => c.pages()).find(p => p.url().startsWith('http'));
  if (!page) throw new Error('No browser page available. Vagaro re-login required.');

  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.setBypassServiceWorker', { bypass: true });

  try {
    const firstResponse = nextReportResponse(cdp);
    await page.goto(REPORT_URL, { waitUntil: 'commit', timeout: 60_000 });
    await page.locator('.pagination-controls select').waitFor({ state: 'attached', timeout: 15_000 }).catch(() => null);

    const firstPage = await firstResponse;
    const first = reportRows(firstPage);
    const pageSize = Number(await page.locator('.pagination-controls select').inputValue().catch(() => '50')) || 50;
    const pageCount = Math.max(1, Math.ceil(first.total / pageSize));
    const rows = [...first.rows];

    for (let pageNumber = 2; pageNumber <= pageCount; pageNumber++) {
      const responsePromise = nextReportResponse(cdp);
      await page.locator('.pagination-controls-left button').filter({ has: page.locator('i.fa-angle-right') }).evaluate(b => b.click());
      const response = await responsePromise;
      rows.push(...reportRows(response).rows);
    }

    fs.writeFileSync(OUTPUT_PATH, JSON.stringify({
      source: 'vagaro-transactions-report',
      exportedAt: new Date().toISOString(),
      totalRecords: rows.length,
      rows,
    }), { mode: 0o600 });

    process.stdout.write(JSON.stringify({ status: 'ok', output: OUTPUT_PATH, records: rows.length }) + '\n');
  } finally {
    await cdp.send('Network.setBypassServiceWorker', { bypass: false }).catch(() => {});
    await cdp.detach().catch(() => {});
    // This is a shared authenticated browser. Letting the process exit
    // disconnects Playwright without destroying the session.
  }
}

main().catch(e => { process.stderr.write(`Transactions export failed: ${e.message}\n`); process.exit(1); });
