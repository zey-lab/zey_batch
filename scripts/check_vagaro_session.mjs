#!/usr/bin/env node
/** Verify the running, private Vagaro browser daemon without exposing PII. */

import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '/opt/data/browser-worker/node_modules/playwright');
const CDP_URL = process.env.VAGARO_CDP_URL || 'http://127.0.0.1:9222';
const PROTECTED_URL = 'https://us04.vagaro.com/merchants/calendar/v3';

async function main() {
  const browser = await chromium.connectOverCDP(CDP_URL);
  const page = browser.contexts()
    .flatMap((context) => context.pages())
    .find((candidate) => candidate.url().startsWith('http'));

  if (!page) {
    throw new Error('No browser page is available. Vagaro re-login is required.');
  }

  await page.goto(PROTECTED_URL, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await page.waitForFunction(
    () => document.body.innerText.includes('Zey Brow & Wax'),
    { timeout: 10_000 },
  ).catch(() => null);
  const authenticated = page.url().includes('/merchants/') && await page.evaluate(
    () => document.body.innerText.includes('Zey Brow & Wax'),
  );

  // This is an externally managed browser. Closing a CDP connection here
  // would terminate the shared authenticated browser before report exports.
  if (!authenticated) {
    throw new Error('Vagaro session is not authenticated. Re-login is required.');
  }

  process.stdout.write(JSON.stringify({
    status: 'ok',
    authenticated: true,
    checkedAt: new Date().toISOString(),
  }) + '\n');
  // Playwright's CDP socket can keep the Node event loop alive after the
  // externally managed browser has been checked. Disconnect this short-lived
  // probe explicitly without closing the remote browser.
  process.exit(0);
}

main().catch((error) => {
  process.stderr.write(`Vagaro session check failed: ${error.message}\n`);
  process.exit(1);
});
