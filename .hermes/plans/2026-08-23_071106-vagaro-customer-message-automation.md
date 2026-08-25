# Vagaro Customer-Message Automation Implementation Plan

> **For Hermes:** Implement in small TDD slices. No live SMS may be enabled unless every production-readiness gate below is verified.

**Goal:** Build a daily 6:00 PM America/Chicago customer-data pipeline that acquires customer data from Vagaro, validates and merges it safely, synchronizes Twilio consent, performs campaign processing in dry-run mode, and can later be promoted to a controlled live run.

**Architecture:** Use Vagaro’s official API as the preferred source because it is less fragile and avoids browser/session failure. Normalize API responses into the existing customer workbook schema. Implement a Playwright export adapter only as a fallback if the API does not expose every required field or account access is unavailable. Both sources feed the same staging, validation, locking, merge, opt-out, campaign, archive, and run-report pipeline.

**Tech stack:** Python 3.11+, `uv`, pandas/openpyxl, Twilio SDK, standard-library `unittest`, GitHub Actions, optional Playwright browser automation, Hermes cron for the 6 PM CT schedule.

---

## Non-negotiable production safeguards

1. The scheduled command must reject a live (`DRY_RUN=false`) configuration by default.
2. No source file may replace `data/CustomersList.xlsx` until schema, mobile-number, row-count, and freshness validation pass.
3. A filesystem lock must prevent overlapping executions.
4. Opt-out synchronization must complete successfully before any campaign processing. Failure must abort the run.
5. Each run must produce a structured, redacted report with source, export timestamp, row counts, validation decision, campaign totals, and failure reason.
6. Raw customer exports, merged state, and campaign workbooks are customer data: keep them out of Git, redact phone numbers from routine logs, and retain only the configured local archive.
7. Live sending is a separate promotion step after dry-run acceptance—not an environment variable silently changed in a scheduled context.

## Current repository findings

- `CampaignManager` already merges the daily `CustomersList.xlsx` with `CustomersList_old.xlsx`, filters opt-outs, selects campaigns, sends through Twilio, and archives inputs.
- The CLI has interactive freshness and live-send confirmations, which makes it unsuitable for a scheduler.
- Current `run.sh` invokes the package opt-out sync then the interactive CLI.
- A legacy root `sync_opt_outs.py` duplicates the package implementation and must not be used by the new automation path.
- Existing tests are setup checks only; they are not an automated regression suite and refer to missing sample workbooks.
- The default `config.yml` supports Excel and CSV inputs, although working filenames are currently Excel.

## Required account validation before source implementation

1. Confirm whether the Zey Brow & Wax Vagaro owner account has official API credentials/partner access.
2. Verify, against a small authorized API read, that the API yields all fields required by campaigns:
   - `Mobile`
   - `First Name` / `Last Name`
   - `Last Visited` (or sufficient appointment/check-out data to calculate it)
   - `Birthdate`
   - `Customer Since`
3. If any required field is absent, record the exact gap and use the Playwright export adapter for that field set instead.
4. Never paste a Vagaro password, MFA code, API secret, Twilio credential, or customer spreadsheet into chat or Git. Store approved credentials only in the local ignored `.env`/secret store.

---

## Task 1: Establish executable test infrastructure

**Objective:** Replace the setup-only confidence model with a deterministic test command that runs in CI and locally.

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_automation.py`
- Create: `tests/test_customer_merger.py`
- Create: `tests/test_campaign_processing.py`
- Modify: `README.md`
- Modify: `test.sh`

**Step 1: Write failing tests**
- Add `unittest` cases that import the new automation module and assert the intended public interfaces.
- Add regression fixtures for merge preservation, opted-out exclusion, campaign rank behavior, and review/birthday filtering.

**Step 2: Verify RED**

Run:
```bash
uv run python -m unittest discover -s tests -v
```

Expected: failure only because the requested implementation does not yet exist, never because tests cannot import the project package.

**Step 3: Make the test runner canonical**
- Use standard-library `unittest`; no unnecessary new test framework dependency is needed.
- Change `test.sh` and README instructions to use the canonical command.

**Step 4: Verify GREEN**

Run:
```bash
uv run python -m unittest discover -s tests -v
uv run python -m compileall -q src
```

**Acceptance:** CI and developer commands can execute all tracked tests without real Twilio/Vagaro access or customer data.

## Task 2: Create export validation as an isolated, tested component

**Objective:** Ensure only a usable customer export can be accepted for campaign processing.

**Files:**
- Create: `src/sms_campaign/automation.py`
- Modify: `tests/test_automation.py`

**Step 1: Write failing tests, one behavior at a time**
1. A valid Excel export with nonempty `Mobile` values returns a report with correct row/valid-mobile counts.
2. Missing `Mobile` raises `ExportValidationError`.
3. An empty export raises `ExportValidationError`.
4. An export with no usable mobiles raises `ExportValidationError`.
5. A stale export exceeds configured maximum age and is rejected.
6. The configured minimum row-count guard rejects unexpected truncation.

**Step 2: Verify RED after each case**

Run targeted tests:
```bash
uv run python -m unittest tests.test_automation.TestCustomerExportValidator.test_export_missing_mobile_column_is_rejected -v
```

**Step 3: Implement the smallest code to pass**
- Define `ExportValidationError`, immutable `ExportValidationReport`, and `CustomerExportValidator`.
- Accept `.xlsx` and `.csv` only through existing `FileHandler` behavior where practical.
- Count valid normalized mobile values without logging full numbers.

**Step 4: Verify GREEN**

Run:
```bash
uv run python -m unittest tests.test_automation -v
```

**Acceptance:** invalid inputs cannot reach `CampaignManager`.

## Task 3: Add a non-interactive dry-run scheduler entry point

**Objective:** Create an unattended command that is safe by construction.

**Files:**
- Create: `src/sms_campaign/scheduled_runner.py`
- Modify: `src/sms_campaign/automation.py`
- Modify: `src/sms_campaign/utils/config.py`
- Modify: `config/config.yml`
- Modify: `.env.example`
- Modify: `tests/test_scheduled_runner.py`

**Step 1: Write failing tests**
1. A live configuration raises `AutomationSafetyError` before touching Twilio or campaign state.
2. A dry-run configuration is accepted.
3. A locked run is rejected with an actionable error.
4. An invalid export aborts before campaign manager invocation.
5. A successful dry-run returns a structured summary.
6. Opt-out sync failure aborts the run.

**Step 2: Verify RED**

Run:
```bash
uv run python -m unittest tests.test_scheduled_runner -v
```

**Step 3: Implement minimal behavior**
- Add `ensure_dry_run(config)` and a `RunLock` context manager.
- Add `ScheduledCampaignRunner` with injected validator, opt-out synchronizer, and manager factory for testability.
- Add config keys: `automation.timezone: America/Chicago`, `automation.max_export_age_hours`, `automation.min_customer_rows`, and lock/report paths.
- Make `python -m sms_campaign.scheduled_runner` return nonzero on any blocked/failed run.

**Step 4: Verify GREEN and interruption behavior**

Run:
```bash
uv run python -m unittest tests.test_scheduled_runner -v
uv run python -m unittest discover -s tests -v
```

**Acceptance:** the runner is automation-safe in dry-run mode and cannot overlap itself.

## Task 4: Make opt-out sync a composable, fail-closed service

**Objective:** Remove ambiguity between legacy and package sync code and guarantee consent protection.

**Files:**
- Modify: `src/sms_campaign/services/opt_out_sync.py`
- Modify: `src/sms_campaign/sync_opt_outs.py`
- Modify: `run.sh`
- Delete or deprecate: `sync_opt_outs.py`, `sync_opt_outs.sh`
- Create: `tests/test_opt_out_sync.py`

**Step 1: Write failing tests**
1. Latest STOP status marks a matching normalized customer opted out.
2. Latest START status clears opt-out only when it is the newest consent event.
3. Transport/API failure raises a dedicated failure rather than returning an empty successful update.
4. The scheduled runner does not run campaigns after sync failure.

**Step 2: Verify RED**

Run:
```bash
uv run python -m unittest tests.test_opt_out_sync -v
```

**Step 3: Implement**
- Return explicit result objects rather than calling `sys.exit` from core code.
- Keep `sys.exit` only at the thin CLI boundary.
- Retain normalized audit counts, not customer PII, in logs.

**Step 4: Verify GREEN**

Run:
```bash
uv run python -m unittest tests.test_opt_out_sync -v
```

**Acceptance:** consent failure blocks the campaign pipeline reliably.

## Task 5: Build the API-first Vagaro source adapter

**Objective:** Download all required customer facts through authorized official APIs and stage a normalized export.

**Precondition:** Approved Vagaro API credentials and documented endpoint/scope confirmation. Do not guess endpoint shapes from community-modeled schemas.

**Files:**
- Create: `src/sms_campaign/sources/base.py`
- Create: `src/sms_campaign/sources/vagaro_api.py`
- Modify: `src/sms_campaign/scheduled_runner.py`
- Modify: `src/sms_campaign/utils/config.py`
- Modify: `.env.example`
- Create: `tests/test_vagaro_api_source.py`

**Step 1: Write failing adapter-contract tests**
1. Paginated customer responses are fully consumed.
2. Customer/appointment facts normalize to the current workbook schema.
3. Required-field gaps fail before staging.
4. An incomplete page or rate-limit failure retries within a bounded policy and then fails closed.
5. The staged output passes `CustomerExportValidator`.

**Step 2: Verify RED**

Run:
```bash
uv run python -m unittest tests.test_vagaro_api_source -v
```

**Step 3: Implement against verified official docs only**
- Obtain and cache token according to the documented expiry and scope.
- Implement explicit timeouts, pagination, bounded retries, and minimal PII logging.
- Write first to a uniquely named staging file, validate it, then atomically place the validated working file.

**Step 4: API sandbox/authorized verification**
- Run a read-only, small-page API request with owner-approved credentials.
- Compare normalized fields and row count with one manual Vagaro export.
- Never store raw API responses in Git or Discord.

**Acceptance:** a real authorized API extraction produces a validated file that is equivalent in required fields to the manual export.

## Task 6: Implement Playwright export fallback only if required

**Objective:** Automate the official web export when the API cannot meet the required data contract.

**Precondition:** Owner logs into the approved persistent browser profile. The agent never enters passwords or MFA codes.

**Files:**
- Create: `src/sms_campaign/sources/vagaro_browser.py`
- Modify: `src/sms_campaign/sources/base.py`
- Modify: `src/sms_campaign/scheduled_runner.py`
- Create: `tests/test_vagaro_browser_contract.py`

**Step 1: Write contract tests**
1. Missing authenticated session produces an explicit `AuthenticationRequired` result, not a silent empty export.
2. A downloaded file is staged and passed through the same validator.
3. Changed browser selectors cause failure with diagnostics and do not replace the working export.

**Step 2: Verify RED, then implement the minimum adapter**
- Browser steps: navigate to Customers report, run unfiltered report, select Export Excel, wait for completed download.
- Capture sanitized screenshots/DOM diagnostics only on failure; never upload customer data.

**Step 3: Verify with an owner-authorized manual session**
- Manually observe the report and download once.
- Run browser export in dry-run acquisition mode.
- Compare required columns, row count, and freshness against the observed export.

**Acceptance:** browser fallback is explicit, observable, and cannot silently return stale/partial data.

## Task 7: Add reporting, archive policy, and production-readiness gates

**Objective:** Make every run auditable and prevent accidental promotion.

**Files:**
- Create: `src/sms_campaign/services/run_report.py`
- Modify: `src/sms_campaign/scheduled_runner.py`
- Modify: `config/config.yml`
- Modify: `README.md`
- Create: `tests/test_run_report.py`

**Tests:**
1. Success reports exclude phone numbers and message bodies.
2. Failure reports state the exact stage that blocked the run.
3. Archive names are timestamped and source-tagged.
4. `AUTOMATION_LIVE_ENABLED` absent/false always retains dry-run behavior.
5. Live enablement requires a separate, deliberate command/config and cannot be selected by cron invocation alone.

**Acceptance:** an operator can answer what happened without exposing customer data, and cron cannot accidentally start live messaging.

## Task 8: Add CI and operational documentation

**Objective:** Ensure every PR runs deterministic checks and the handoff is executable.

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `README.md`
- Create: `docs/OPERATIONS.md`
- Modify: `.gitignore`

**CI workflow:**
```yaml
name: CI
on: [pull_request, push]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --locked
      - run: uv run python -m compileall -q src
      - run: uv run python -m unittest discover -s tests -v
```

**Documentation must include:**
- Required source fields and customer-data handling policy.
- API-first decision and browser fallback procedure.
- Exact dry-run command, expected report, and failure handling.
- Local 6 PM CT schedule and why it is not installed until acceptance.
- Production promotion checklist and rollback to dry-run.

**Acceptance:** CI passes on the PR, no secrets/customer data are tracked, and a new operator can run dry-run safely from documentation.

## Task 9: End-to-end dry-run rehearsal and production promotion decision

**Objective:** Prove the full path on representative, authorized data before enabling live messages.

**Steps:**
1. Run a fresh acquisition through the selected source adapter.
2. Validate against manual export count/schema expectations.
3. Execute the scheduled runner in dry-run mode.
4. Review run report, excluded opt-outs, candidates, campaigns, archive output, and error handling.
5. Repeat at least three daily runs, including one controlled invalid-export test and one controlled opt-out-sync failure test.
6. Document observed results and resolve discrepancies.
7. Obtain explicit approval to change from dry-run to live mode.

**Do not enable live mode until all checks pass.**

## Task 10: Schedule after acceptance

**Objective:** Install the recurring schedule only after Tasks 1–9 have passed.

**Schedule:** `0 18 * * *` in `America/Chicago`.

**Implementation:** Use the Hermes cron scheduler with a local script that runs the tested `ScheduledCampaignRunner` in dry-run mode initially. The job should deliver a concise run report to this Discord thread/channel and remain fail-closed.

**Post-install verification:**
1. Fire one manual dry-run scheduler invocation.
2. Verify its exact delivery, exit status, report, and archive output.
3. Verify duplicate-lock behavior.
4. Confirm the next scheduled occurrence resolves to 6:00 PM America/Chicago.

---

## Production exit criteria

The system may be promoted from dry-run only when all are true:

- Official API source is verified with required fields, or the owner-authorized browser fallback is tested and stable.
- Unit/regression/integration tests are green locally and in GitHub Actions.
- Three clean daily dry-run rehearsals complete with representative data.
- Invalid/stale input, source/auth failure, opt-out failure, and lock contention are proven to fail closed.
- Customer exports/credentials are absent from Git history and routine run messages.
- A rollback procedure returns the scheduler to dry-run with one configuration change.
- The business owner explicitly approves live sending.

## Known risks and mitigations

| Risk | Mitigation |
|---|---|
| Vagaro API lacks campaign-required activity fields | Verify data contract before implementation; use Playwright fallback only if needed. |
| Session expiry/MFA breaks browser export | Fail closed; notify for owner reauthentication; never handle secrets in chat. |
| Partial/empty export causes a blast to wrong cohort | Freshness, schema, mobile-count, minimum-row guards; atomic staging. |
| Duplicate scheduled runs | Non-blocking process lock; explicit failed status. |
| Twilio STOP is missed | Sync must finish successfully before campaigns; sync errors abort the run. |
| Interrupted sends create duplicate messages | Preserve per-send state; add idempotency/run manifests before live promotion. |
| Customer-data exposure | Git ignore, local archive, redacted logs/reports, no uploads to Discord. |
| DST timing errors | `America/Chicago` timezone-aware scheduling, not a fixed UTC value. |
