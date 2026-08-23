from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from sms_campaign.automation import (
    AutomationSafetyError,
    CustomerExportValidator,
    ExportValidationError,
    ensure_dry_run,
)


class _Config:
    def __init__(self, dry_run: bool) -> None:
        self.dry_run = dry_run


class TestAutomationSafety(unittest.TestCase):
    def test_live_configuration_is_rejected_for_automation(self) -> None:
        with self.assertRaises(AutomationSafetyError):
            ensure_dry_run(_Config(dry_run=False))

    def test_dry_run_configuration_is_accepted_for_automation(self) -> None:
        ensure_dry_run(_Config(dry_run=True))


class TestCustomerExportValidator(unittest.TestCase):
    def test_valid_export_requires_a_nonempty_mobile_column(self) -> None:
        with TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "CustomersList.xlsx"
            pd.DataFrame(
                {
                    "Mobile": ["+15550000001", "+15550000002"],
                    "First Name": ["Ana", "Ben"],
                }
            ).to_excel(export_path, index=False)

            report = CustomerExportValidator().validate(export_path)

        self.assertEqual(report.row_count, 2)
        self.assertEqual(report.valid_mobile_count, 2)

    def test_export_missing_mobile_column_is_rejected(self) -> None:
        with TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "CustomersList.xlsx"
            pd.DataFrame({"First Name": ["Ana"]}).to_excel(export_path, index=False)

            with self.assertRaisesRegex(ExportValidationError, "Mobile"):
                CustomerExportValidator().validate(export_path)

    def test_export_with_no_usable_mobile_numbers_is_rejected(self) -> None:
        with TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "CustomersList.xlsx"
            pd.DataFrame({"Mobile": [None, ""], "First Name": ["Ana", "Ben"]}).to_excel(
                export_path, index=False
            )

            with self.assertRaisesRegex(ExportValidationError, "usable mobile"):
                CustomerExportValidator().validate(export_path)

    def test_export_below_minimum_row_count_is_rejected(self) -> None:
        with TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "CustomersList.xlsx"
            pd.DataFrame({"Mobile": ["+15550000001"], "First Name": ["Ana"]}).to_excel(
                export_path, index=False
            )

            with self.assertRaisesRegex(ExportValidationError, "minimum row count"):
                CustomerExportValidator(minimum_rows=2).validate(export_path)

    def test_export_older_than_maximum_age_is_rejected(self) -> None:
        with TemporaryDirectory() as temp_dir:
            export_path = Path(temp_dir) / "CustomersList.xlsx"
            pd.DataFrame({"Mobile": ["+15550000001"], "First Name": ["Ana"]}).to_excel(
                export_path, index=False
            )
            old_timestamp = 1_704_067_200  # 2024-01-01T00:00:00Z
            export_path.touch()
            import os
            os.utime(export_path, (old_timestamp, old_timestamp))

            with self.assertRaisesRegex(ExportValidationError, "maximum age"):
                CustomerExportValidator(maximum_age_hours=24).validate(export_path)


if __name__ == "__main__":
    unittest.main()
