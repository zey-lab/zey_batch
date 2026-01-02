#!/usr/bin/env python3
"""
Sync Opt-Outs from Twilio

This script fetches opt-out (STOP) replies from Twilio and updates your customer list.
Run this weekly or before each campaign to keep your opt-out list in sync.

Usage:
    uv run python sync_opt_outs.py

What it does:
1. Fetches recent messages sent TO your Twilio number
2. Identifies STOP/START replies
3. Updates CustomersList.xlsx with opt-out status
4. Logs all changes
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from twilio.rest import Client

# Load environment
load_dotenv()

# Configuration
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')

# File paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
CUSTOMERS_FILE = DATA_DIR / "CustomersList.xlsx"

# Column names (must match your Excel file)
PHONE_COLUMN = "Mobile"
OPT_OUT_COLUMN = "SMS_Opt_Out"
OPT_OUT_DATE_COLUMN = "Opt_Out_Date"

# How far back to check (days)
LOOKBACK_DAYS = 30

# STOP keywords
STOP_KEYWORDS = ['STOP', 'STOPALL', 'UNSUBSCRIBE', 'CANCEL', 'END', 'QUIT']
START_KEYWORDS = ['START', 'YES', 'UNSTOP']


def print_header(text: str):
    """Print a styled header."""
    print(f"\n{'=' * 60}")
    print(f"  {text}")
    print(f"{'=' * 60}\n")


def print_success(text: str):
    """Print success message."""
    print(f"✓ {text}")


def print_warning(text: str):
    """Print warning message."""
    print(f"⚠ {text}")


def print_error(text: str):
    """Print error message."""
    print(f"✗ {text}")


def normalize_phone(phone: str) -> str:
    """Normalize phone number for comparison."""
    if not phone:
        return ""
    # Remove all non-numeric characters
    return ''.join(c for c in str(phone) if c.isdigit())


def validate_config():
    """Validate that all required configuration is present."""
    missing = []

    if not TWILIO_ACCOUNT_SID:
        missing.append("TWILIO_ACCOUNT_SID")
    if not TWILIO_AUTH_TOKEN:
        missing.append("TWILIO_AUTH_TOKEN")
    if not TWILIO_PHONE_NUMBER:
        missing.append("TWILIO_PHONE_NUMBER")

    if missing:
        print_error("Missing required environment variables:")
        for var in missing:
            print(f"  - {var}")
        print("\nPlease configure these in your .env file")
        return False

    if not CUSTOMERS_FILE.exists():
        print_error(f"Customer list not found: {CUSTOMERS_FILE}")
        print("Please ensure your CustomersList.xlsx is in the data/ directory")
        return False

    return True


def fetch_opt_out_replies(client: Client) -> tuple[set[str], set[str]]:
    """
    Fetch STOP and START replies from Twilio.

    Returns:
        Tuple of (opted_out_numbers, opted_in_numbers)
    """
    print_header("Fetching Messages from Twilio")

    # Calculate date range
    date_from = datetime.now() - timedelta(days=LOOKBACK_DAYS)

    print(f"Checking messages from last {LOOKBACK_DAYS} days...")
    print(f"From: {date_from.strftime('%Y-%m-%d')}")
    print(f"To: {datetime.now().strftime('%Y-%m-%d')}")
    print(f"Twilio Number: {TWILIO_PHONE_NUMBER}\n")

    try:
        # Fetch messages sent TO your Twilio number (customer replies)
        messages = client.messages.list(
            to=TWILIO_PHONE_NUMBER,
            date_sent_after=date_from,
            limit=1000
        )

        print(f"Found {len(messages)} total incoming messages\n")

    except Exception as e:
        print_error(f"Failed to fetch messages from Twilio: {e}")
        return set(), set()

    # Process messages
    opted_out_numbers = set()
    opted_in_numbers = set()

    for msg in messages:
        if not msg.body:
            continue

        body = msg.body.strip().upper()
        from_number = msg.from_

        # Check for STOP keywords
        if body in STOP_KEYWORDS:
            opted_out_numbers.add(from_number)
            print(f"  STOP from: {from_number} on {msg.date_sent.strftime('%Y-%m-%d %H:%M')}")

        # Check for START keywords
        elif body in START_KEYWORDS:
            opted_in_numbers.add(from_number)
            print(f"  START from: {from_number} on {msg.date_sent.strftime('%Y-%m-%d %H:%M')}")

    print()
    print_success(f"Found {len(opted_out_numbers)} opt-out requests")
    print_success(f"Found {len(opted_in_numbers)} opt-in requests")

    return opted_out_numbers, opted_in_numbers


def update_customer_list(opted_out_numbers: set[str], opted_in_numbers: set[str]) -> dict:
    """
    Update customer list with opt-out status.

    Returns:
        Dictionary with update statistics
    """
    print_header("Updating Customer List")

    # Load customer list
    try:
        df = pd.read_excel(CUSTOMERS_FILE)
        print(f"Loaded {len(df)} customers from {CUSTOMERS_FILE.name}\n")
    except Exception as e:
        print_error(f"Failed to read customer list: {e}")
        return {}

    # Verify required columns exist
    if PHONE_COLUMN not in df.columns:
        print_error(f"Column '{PHONE_COLUMN}' not found in customer list")
        return {}

    # Add opt-out columns if they don't exist
    if OPT_OUT_COLUMN not in df.columns:
        df[OPT_OUT_COLUMN] = 'No'
        print(f"Added column: {OPT_OUT_COLUMN}")

    if OPT_OUT_DATE_COLUMN not in df.columns:
        df[OPT_OUT_DATE_COLUMN] = ''
        print(f"Added column: {OPT_OUT_DATE_COLUMN}\n")

    # Statistics
    stats = {
        'total_customers': len(df),
        'opted_out_updated': 0,
        'opted_in_updated': 0,
        'not_found': 0
    }

    # Process opt-outs
    if opted_out_numbers:
        print("Processing opt-outs...")
        for phone in opted_out_numbers:
            phone_normalized = normalize_phone(phone)

            # Find matching customer
            mask = df[PHONE_COLUMN].astype(str).apply(normalize_phone) == phone_normalized

            if mask.any():
                # Update opt-out status
                df.loc[mask, OPT_OUT_COLUMN] = 'Yes'
                df.loc[mask, OPT_OUT_DATE_COLUMN] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                customer_name = df.loc[mask, 'First Name'].values[0] if 'First Name' in df.columns else "Unknown"
                print_success(f"Marked as opted out: {phone} ({customer_name})")
                stats['opted_out_updated'] += 1
            else:
                print_warning(f"Phone not found in customer list: {phone}")
                stats['not_found'] += 1
        print()

    # Process opt-ins
    if opted_in_numbers:
        print("Processing opt-ins...")
        for phone in opted_in_numbers:
            phone_normalized = normalize_phone(phone)

            # Find matching customer
            mask = df[PHONE_COLUMN].astype(str).apply(normalize_phone) == phone_normalized

            if mask.any():
                # Clear opt-out status
                df.loc[mask, OPT_OUT_COLUMN] = 'No'
                df.loc[mask, OPT_OUT_DATE_COLUMN] = ''

                customer_name = df.loc[mask, 'First Name'].values[0] if 'First Name' in df.columns else "Unknown"
                print_success(f"Re-subscribed: {phone} ({customer_name})")
                stats['opted_in_updated'] += 1
            else:
                print_warning(f"Phone not found in customer list: {phone}")
                stats['not_found'] += 1
        print()

    # Save updated customer list
    if stats['opted_out_updated'] > 0 or stats['opted_in_updated'] > 0:
        try:
            df.to_excel(CUSTOMERS_FILE, index=False)
            print_success(f"Customer list updated: {CUSTOMERS_FILE}")
        except Exception as e:
            print_error(f"Failed to save customer list: {e}")
            return stats
    else:
        print("No changes to save")

    return stats


def print_summary(stats: dict):
    """Print summary of sync operation."""
    print_header("Sync Summary")

    print(f"Total customers:       {stats.get('total_customers', 0)}")
    print(f"Opted out:            {stats.get('opted_out_updated', 0)}")
    print(f"Opted in:             {stats.get('opted_in_updated', 0)}")
    print(f"Not found in list:    {stats.get('not_found', 0)}")

    total_changes = stats.get('opted_out_updated', 0) + stats.get('opted_in_updated', 0)

    print()
    if total_changes > 0:
        print_success(f"✅ Sync completed successfully! {total_changes} customer(s) updated")
    else:
        print("✅ Sync completed - no changes needed")
    print()


def main():
    """Main sync operation."""
    print_header("SMS Opt-Out Sync Tool")
    print("This tool syncs STOP/START replies from Twilio to your customer list\n")

    # Validate configuration
    if not validate_config():
        print("\n❌ Configuration validation failed")
        sys.exit(1)

    # Initialize Twilio client
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        print_success("Connected to Twilio")
    except Exception as e:
        print_error(f"Failed to connect to Twilio: {e}")
        sys.exit(1)

    # Fetch opt-out/opt-in replies
    opted_out_numbers, opted_in_numbers = fetch_opt_out_replies(client)

    # Update customer list
    if opted_out_numbers or opted_in_numbers:
        stats = update_customer_list(opted_out_numbers, opted_in_numbers)
        print_summary(stats)
    else:
        print_header("Result")
        print("No STOP or START messages found in the last {} days".format(LOOKBACK_DAYS))
        print("Your customer list is already in sync! ✓\n")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠ Sync cancelled by user")
        sys.exit(1)
    except Exception as e:
        print_error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
