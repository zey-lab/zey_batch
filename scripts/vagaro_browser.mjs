#!/usr/bin/env node
/**
 * Create a Browser Use Cloud browser session for Vagaro login.
 * Returns live URL for user authentication and CDP URL for extraction.
 */

import { execSync } from 'node:child_process';

const API_KEY = process.env.BROWSER_USE_API_KEY;
if (!API_KEY) {
  process.stderr.write('BROWSER_USE_API_KEY not set\n');
  process.exit(1);
}

const TIMEOUT_MIN = parseInt(process.env.BROWSER_TIMEOUT || '120', 10);

function createBrowser() {
  const result = execSync(
    `curl -sS https://api.browser-use.com/api/v4/browsers ` +
    `-H "X-Browser-Use-API-Key: ${API_KEY}" ` +
    `-H "Content-Type: application/json" ` +
    `-d '{"timeout":${TIMEOUT_MIN}}'`,
    { encoding: 'utf-8', timeout: 30_000 }
  );
  return JSON.parse(result);
}

function stopBrowser(id) {
  try {
    execSync(
      `curl -sS -X PATCH "https://api.browser-use.com/api/v4/browsers/${id}" ` +
      `-H "X-Browser-Use-API-Key: ${API_KEY}" ` +
      `-H "Content-Type: application/json" ` +
      `-d '{"action":"stop"}'`,
      { encoding: 'utf-8', timeout: 15_000 }
    );
  } catch {}
}

function navigateToVagaro(cdpUrl) {
  const result = execSync(
    `node -e "const {chromium}=require('playwright');` +
    `(async()=>{` +
    `const b=await chromium.connectOverCDP('${cdpUrl}');` +
    `const p=b.contexts()[0]?.pages()[0]||await b.contexts()[0].newPage();` +
    `await p.goto('https://www.vagaro.com/login',{waitUntil:'domcontentloaded',timeout:30000});` +
    `console.log(JSON.stringify({ok:true,title:await p.title()}));` +
    `await b.close();` +
    `})().catch(e=>{console.error(e.message);process.exit(1)})"`,
    { encoding: 'utf-8', timeout: 60_000, cwd: '/opt/data/browser-worker' }
  );
  return JSON.parse(result);
}

function checkAuth(cdpUrl) {
  const result = execSync(
    `node -e "const {chromium}=require('playwright');` +
    `(async()=>{` +
    `const b=await chromium.connectOverCDP('${cdpUrl}');` +
    `const p=b.contexts().flatMap(c=>c.pages()).find(p=>p.url().includes('vagaro'));` +
    `const url=p?.url()||'none';` +
    `const auth=url.includes('/merchants/');` +
    `console.log(JSON.stringify({url,authenticated:auth}));` +
    `await b.close();` +
    `})().catch(e=>{console.log(JSON.stringify({url:'error',authenticated:false}));process.exit(0)})"`,
    { encoding: 'utf-8', timeout: 30_000, cwd: '/opt/data/browser-worker' }
  );
  return JSON.parse(result);
}

// Main
const action = process.argv[2] || 'create';

if (action === 'create') {
  const browser = createBrowser();
  if (!browser.id) {
    process.stderr.write('Failed to create browser: ' + JSON.stringify(browser) + '\n');
    process.exit(1);
  }
  // Navigate to Vagaro login
  try { navigateToVagaro(browser.cdpUrl); } catch {}
  process.stdout.write(JSON.stringify({
    status: 'ok',
    browser_id: browser.id,
    live_url: browser.liveUrl,
    cdp_url: browser.cdpUrl,
    timeout_at: browser.timeoutAt,
  }) + '\n');
} else if (action === 'check') {
  const cdpUrl = process.argv[3];
  if (!cdpUrl) { process.stderr.write('Usage: vagaro_browser.mjs check <cdp_url>\n'); process.exit(1); }
  const result = checkAuth(cdpUrl);
  process.stdout.write(JSON.stringify(result) + '\n');
} else if (action === 'stop') {
  const browserId = process.argv[3];
  if (!browserId) { process.stderr.write('Usage: vagaro_browser.mjs stop <browser_id>\n'); process.exit(1); }
  stopBrowser(browserId);
  process.stdout.write(JSON.stringify({ status: 'stopped' }) + '\n');
} else {
  process.stderr.write('Usage: vagaro_browser.mjs [create|check|stop]\n');
  process.exit(1);
}
