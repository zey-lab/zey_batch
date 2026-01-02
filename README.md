# SMS Campaign Manager (Zey Batch)

A robust, automated SMS campaign management system designed to handle customer engagement, opt-out compliance, and personalized messaging using Twilio.

## Features

*   **Automated Campaigns:** Send personalized SMS messages based on customer data (Last Visit, Birthday, Anniversary).
*   **Smart Opt-Out Management:** Automatically syncs `STOP`/`START` replies from Twilio and updates the customer database to ensure compliance.
*   **Data Merging:** Intelligently merges new customer lists with existing data, preserving history and opt-out status.
*   **Cost Optimization:** Analyzes message length and encoding (GSM-7 vs Unicode) to minimize segment costs.
*   **Safety First:** Includes "Dry Run" mode and Test Number filtering to prevent accidental blasts.

## Prerequisites

*   Python 3.11 or higher
*   [uv](https://github.com/astral-sh/uv) (Fast Python package installer and resolver)
*   A Twilio Account (SID, Auth Token, and Phone Number)

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

### Development
To run tests or check for security leaks:
```bash
uv run python test_setup.py
```

## Project Structure

*   `src/sms_campaign/`: Source code for the application.
*   `config/`: Configuration files (`config.yml`).
*   `data/`: Stores customer lists and campaign definitions.
*   `logs/`: Execution logs.