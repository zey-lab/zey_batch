# SMS Campaign Manager (Zey Batch)

A robust, automated SMS campaign management system designed to handle customer engagement, opt-out compliance, and personalized messaging using Twilio.

## Features

*   **Automated Campaigns:** Send personalized SMS messages based on customer data (Last Visit, Birthday, Anniversary).
*   **Smart Opt-Out Management:** Automatically syncs `STOP`/`START` replies from Twilio and updates the customer database to ensure compliance.
*   **Data Merging:** Intelligently merges new customer lists with existing data, preserving history and opt-out status.
*   **Cost Optimization:** Analyzes message length and encoding (GSM-7 vs Unicode) to minimize segment costs.
*   **Safety First:** Includes "Dry Run" mode and Test Number filtering to prevent accidental blasts.
*   **Separate Email Channel:** Optional Gmail API campaigns use their own opt-out and `email_history` records; Twilio is never used for email.

## Prerequisites

*   Python 3.11 or higher
*   [uv](https://github.com/astral-sh/uv) (Fast Python package installer and resolver)
*   A Twilio Account (SID, Auth Token, and Phone Number) for SMS
*   A Google mailbox authorized for Gmail API sending for email

## Installation & Setup

This project uses `uv` for dependency management and virtual environment creation.

1.  **Clone the repository:**
    ```bash
    git clone <repository-url>
    cd zey_batch
    ```

2.  **Install dependencies:**
    This command will create a virtual environment and install all required packages.
    ```bash
    uv sync
    ```

3.  **Configure Environment Variables:**
    Copy the example environment file and add your Twilio credentials.
    ```bash
    cp .env.example .env
    ```
    Edit `.env` and fill in your details:
    ```ini
    TWILIO_ACCOUNT_SID=your_sid_here
    TWILIO_AUTH_TOKEN=your_token_here
    TWILIO_PHONE_NUMBER=+1234567890
    # Optional: Add test numbers to restrict sending during development
    TEST_PHONE_NUMBERS=+15550001111,+15550002222
    ```

4.  **Prepare Data:**
    *   Place your customer list in `data/CustomersList.xlsx`.
    *   Configure your campaigns in `data/campaigns.xlsx`.
    *   (See `data/archive/*.sample.csv` for format examples).

## Usage

### Running a Campaign
The easiest way to run the system is using the provided shell script. This handles opt-out syncing and campaign execution in one go.

```bash
./run.sh
```

### Manual Execution
You can also run individual components using `uv run`:

*   **Sync Opt-Outs Only:**
    ```bash
    uv run python -m sms_campaign.sync_opt_outs
    ```

*   **Run Campaign Manager Only:**
    ```bash
    uv run python -m sms_campaign.cli
    ```

### Email campaigns

Email is opt-in per campaign and is disabled unless a campaign's `Channels`
column contains `email` or `both`. Add `Email Subject` and optionally `Email
HTML` to the Campaigns sheet. If `Email HTML` is blank, the runner uses a
responsive Zey Brow-colored template. Set `EMAIL_LOGO_URL` only to an approved
public logo asset; the system does not guess a logo path from the website.

The first command is a preview and cannot send mail:

```bash
uv run python scripts/run_email_campaigns.py --campaign-id 1 --test-email owner@example.com
```

Live mode is separately gated by `EMAIL_LIVE_APPROVED=true` and requires a
server-only Gmail OAuth configuration. Use the Google OAuth account that owns
`EMAIL_FROM` (currently planned as `zeybrowwax@gmail.com`) and grant only the
Gmail send scope. Store `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, and
`GMAIL_REFRESH_TOKEN` in the protected service environment; never put them in
Git, a spreadsheet, or an issue comment. A verified Gmail “Send mail as” alias
is required if the visible From address differs from the authenticated mailbox.

```bash
EMAIL_LIVE_APPROVED=true uv run python scripts/run_email_campaigns.py --live --campaign-id 1 --test-email owner@example.com
```

The runner records every live attempt in `email_history` and mirrors that table
to Supabase/Google Sheets on the existing mirror jobs. No live email is sent
as part of installation or deployment.

### Development
To run tests or check for security leaks:
```bash
uv run python test_setup.py
```

### Vagaro webhook receiver

The Cloudflare Tunnel should publish only `https://vagaro-webhook.zeybrow.com/vagaro/webhook` to the loopback receiver at `http://127.0.0.1:8787`. The receiver stores every accepted event in the SQLite `webhook_events` table and deduplicates Vagaro retries by event ID. Customer events remain in the receipt log until the daily full customer snapshot reconciles them.

Set `VAGARO_WEBHOOK_TOKEN` in the server-only `.env` file after Vagaro creates the webhook and displays its verification token. Never commit or share that value. Install `deploy/vagaro-webhook.service` as a systemd service, then check `/healthz` locally before enabling the Vagaro webhook.

## Project Structure

*   `src/sms_campaign/`: Source code for the application.
*   `config/`: Configuration files (`config.yml`).
*   `data/`: Stores customer lists and campaign definitions.
*   `logs/`: Execution logs.
