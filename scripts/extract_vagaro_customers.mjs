#!/usr/bin/env node
/**
 * Extract Vagaro Customer Report via authenticated Chromium daemon.
 * Outputs JSON to data/incoming/vagaro_customers_latest.json
 */

import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '/opt/data/browser-worker/node_modules/playwright');

const CDP_URL = process.env.VAGARO_CDP_URL || 'http://127.0.0.1:9222';
const REPORT_URL = 'https://us04.vagaro.com/merchants/reports/customers/list';
const OUTPUT_DIR = process.env.VAGARO_EXPORT_DIR || path.resolve('data/incoming');
const OUTPUT_PATH = path.join(OUTPUT_DIR, 'vagaro_customers_latest.json');
const REPORT_ENDPOINT = '/merchants/reports/getcustomers';

function nextReportResponse(cdp) {
  return new Promise((resolve, reject) => {
    let targetRequestId;
    const cleanup = () => {
      clearTimeout(timeout);
      cdp.off('Network.responseReceived', responseHandler);
      cdp.off('Network.loadingFinished', finishedHandler);
    };
    const timeout = setTimeout(() => { cleanup(); reject(new Error('Timeout waiting for report response.')); }, 60_000);
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

async function main() {
  fs.mkdirSync(OUTPUT_DIR, { recursive: true, mode: 0o700 });
  const browser = await chromium.connectOverCDP(CDP_URL);
  const page = browser.contexts().flatMap(c => c.pages()).find(p => p.url().startsWith('http'));
  if (!page) throw new Error('No browser page available. Vagaro re-login required.');

  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.setBypassServiceWorker', { bypass: true });

  try {
    await page.goto(REPORT_URL, { waitUntil: 'domcontentloaded', timeout: 60_000 });
    await page.locator('.pagination-controls select').waitFor({ state: 'attached', timeout: 15_000 });

    const firstResponse = nextReportResponse(cdp);
    await page.locator('.pagination-controls select').selectOption('200', { force: true });
    const firstPage = await firstResponse;
    if (!Array.isArray(firstPage.Data)) throw new Error('Invalid customer report response.');

    const totalRecords = Number(firstPage.Data[0]?.TotalRecords ?? firstPage.Data.length);
    const pageSize = 200;
    const pageCount = Math.max(1, Math.ceil(totalRecords / pageSize));
    const rows = [...firstPage.Data];

    for (let pageNumber = 2; pageNumber <= pageCount; pageNumber++) {
      const responsePromise = nextReportResponse(cdp);
      await page.locator('.pagination-controls-left button').filter({ has: page.locator('i.fa-angle-right') }).evaluate(b => b.click());
      const response = await responsePromise;
      if (!Array.isArray(response.Data)) throw new Error(`Page ${pageNumber} invalid.`);
      rows.push(...response.Data);
    }

    const deduplicated = [...new Map(rows.map(r => [r.UserID, r])).values()];
    if (deduplicated.length !== totalRecords) throw new Error(`Count mismatch: expected ${totalRecords}, got ${deduplicated.length}.`);

    fs.writeFileSync(OUTPUT_PATH, JSON.stringify({
      source: 'vagaro-customer-report',
      exportedAt: new Date().toISOString(),
      totalRecords,
      rows: deduplicated,
    }), { mode: 0o600 });

    process.stdout.write(JSON.stringify({ status: 'ok', output: OUTPUT_PATH, records: deduplicated.length, pages: pageCount }) + '\n');
  } finally {
    await cdp.send('Network.setBypassServiceWorker', { bypass: false }).catch(() => {});
    await cdp.detach().catch(() => {});
    await browser.close();
  }
}

main().catch(e => { process.stderr.write(`Export failed: ${e.message}\n`); process.exit(1); });
