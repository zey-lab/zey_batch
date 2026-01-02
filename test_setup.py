#!/usr/bin/env python3
"""Test script to verify the SMS Campaign Manager setup."""

import sys
from pathlib import Path


def test_imports():
    """Test that all required modules can be imported."""
    print("Testing imports...")

    try:
        import pandas
        print("  ✓ pandas")
    except ImportError as e:
        print(f"  ✗ pandas: {e}")
        return False

    try:
        import openpyxl
        print("  ✓ openpyxl")
    except ImportError as e:
        print(f"  ✗ openpyxl: {e}")
        return False

    try:
        from dotenv import load_dotenv
        print("  ✓ python-dotenv")
    except ImportError as e:
        print(f"  ✗ python-dotenv: {e}")
        return False

    try:
        import yaml
        print("  ✓ pyyaml")
    except ImportError as e:
        print(f"  ✗ pyyaml: {e}")
        return False

    try:
        from rich.console import Console
        print("  ✓ rich")
    except ImportError as e:
        print(f"  ✗ rich: {e}")
        return False

    try:
        from twilio.rest import Client
        print("  ✓ twilio")
    except ImportError as e:
        print(f"  ✗ twilio: {e}")
        return False

    try:
        from sms_campaign.utils.config import Config
        print("  ✓ sms_campaign.utils.config")
    except ImportError as e:
        print(f"  ✗ sms_campaign.utils.config: {e}")
        return False

    try:
        from sms_campaign.utils.logger import CampaignLogger
        print("  ✓ sms_campaign.utils.logger")
    except ImportError as e:
        print(f"  ✗ sms_campaign.utils.logger: {e}")
        return False

    try:
        from sms_campaign.manager import CampaignManager
        print("  ✓ sms_campaign.manager")
    except ImportError as e:
        print(f"  ✗ sms_campaign.manager: {e}")
        return False

    return True


def test_config():
    """Test configuration loading."""
    print("\nTesting configuration...")

    try:
        from sms_campaign.utils.config import Config

        config = Config()
        print(f"  ✓ Config loaded")
        print(f"    - Data folder: {config.data_folder}")
        print(f"    - Logs folder: {config.logs_folder}")
        print(f"    - Dry run: {config.dry_run}")

        return True
    except Exception as e:
        print(f"  ✗ Config error: {e}")
        return False


def test_sample_files():
    """Test that sample files exist."""
    print("\nTesting sample files...")

    project_root = Path.cwd()
    data_folder = project_root / "data"

    sample_files = [
        "customers_list_new.sample.xlsx",
        "customers_list_old.sample.xlsx",
        "campaigns.sample.xlsx",
    ]

    all_exist = True
    for filename in sample_files:
        file_path = data_folder / filename
        if file_path.exists():
            print(f"  ✓ {filename}")
        else:
            print(f"  ✗ {filename} not found")
            all_exist = False

    return all_exist


def test_directories():
    """Test that required directories exist."""
    print("\nTesting directories...")

    project_root = Path.cwd()
    required_dirs = [
        "data",
        "config",
        "logs",
        "data/archive",
        "data/delete_temp",
        "src/sms_campaign",
    ]

    all_exist = True
    for dir_name in required_dirs:
        dir_path = project_root / dir_name
        if dir_path.exists():
            print(f"  ✓ {dir_name}/")
        else:
            print(f"  ✗ {dir_name}/ not found")
            all_exist = False

    return all_exist


def test_data_loading():
    """Test loading sample data files."""
    print("\nTesting data loading...")

    try:
        import pandas as pd
        from sms_campaign.utils.file_handler import FileHandler

        project_root = Path.cwd()
        sample_file = project_root / "data" / "customers_list_new.sample.xlsx"

        if not sample_file.exists():
            print(f"  ⚠ Sample file not found, skipping")
            return True

        df = FileHandler.read_dataframe(sample_file)
        print(f"  ✓ Loaded sample customer list: {len(df)} rows")
        print(f"    Columns: {', '.join(df.columns[:5])}...")

        return True
    except Exception as e:
        print(f"  ✗ Data loading error: {e}")
        return False


def main():
    """Run all tests."""
    print("=" * 60)
    print("  SMS Campaign Manager - Setup Test")
    print("=" * 60)
    print()

    tests = [
        ("Imports", test_imports),
        ("Configuration", test_config),
        ("Directories", test_directories),
        ("Sample Files", test_sample_files),
        ("Data Loading", test_data_loading),
    ]

    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"\n  ✗ {test_name} failed with exception: {e}")
            results.append((test_name, False))

    # Summary
    print("\n" + "=" * 60)
    print("  Test Summary")
    print("=" * 60)
    print()

    all_passed = True
    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {test_name}")
        if not result:
            all_passed = False

    print()

    if all_passed:
        print("🎉 All tests passed! Your setup is ready.")
        print()
        print("Next steps:")
        print("  1. Copy .env.example to .env and add your Twilio credentials")
        print("  2. Copy sample files to working files:")
        print("     cp data/customers_list_new.sample.xlsx data/customers_list_new.xlsx")
        print("     cp data/campaigns.sample.xlsx data/campaigns.xlsx")
        print("  3. Run the campaign manager:")
        print("     uv run python -m sms_campaign.cli")
        print()
        return 0
    else:
        print("❌ Some tests failed. Please check the errors above.")
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
