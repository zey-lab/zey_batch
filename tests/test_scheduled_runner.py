from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from sms_campaign.automation import (
    AutomationSafetyError,
    ExportValidationError,
    ExportValidationReport,
    RunAlreadyInProgress,
    RunLock,
    ScheduledCampaignRunner,
)


class _Config:
    def __init__(self, dry_run: bool) -> None:
        self.dry_run = dry_run


class _Validator:
    def __init__(self, events: list[str], error: Exception | None = None) -> None:
        self.events = events
        self.error = error

    def validate(self, _path: Path) -> ExportValidationReport:
        self.events.append("validate")
        if self.error:
            raise self.error
        return ExportValidationReport(row_count=2, valid_mobile_count=2)


class _CampaignManager:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def run(self) -> dict:
        self.events.append("campaign")
        return {"success": True, "total_sent": 2, "total_failed": 0}


class _CustomerStore:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.rows: list[dict] = []

    def sync_dataframe(self, dataframe: pd.DataFrame) -> dict:
        self.events.append("customer_db")
        self.rows = dataframe.to_dict("records")
        return {"inserted": len(dataframe), "updated": 0, "deactivated": 0, "invalid": 0}


class TestScheduledCampaignRunner(unittest.TestCase):
    def test_live_configuration_is_rejected_before_pipeline_starts(self) -> None:
        events: list[str] = []
        runner = ScheduledCampaignRunner(
            config=_Config(dry_run=False),
            export_path=Path("ignored.xlsx"),
            validator=_Validator(events),
            sync_opt_outs=lambda: events.append("sync"),
            campaign_manager=_CampaignManager(events),
            lock_path=Path("/tmp/ignored.lock"),
        )

        with self.assertRaises(AutomationSafetyError):
            runner.run()

        self.assertEqual(events, [])

    def test_invalid_export_aborts_before_sync_or_campaign(self) -> None:
        events: list[str] = []
        runner = ScheduledCampaignRunner(
            config=_Config(dry_run=True),
            export_path=Path("ignored.xlsx"),
            validator=_Validator(events, ExportValidationError("invalid export")),
            sync_opt_outs=lambda: events.append("sync"),
            campaign_manager=_CampaignManager(events),
            lock_path=Path("/tmp/ignored.lock"),
        )

        with self.assertRaises(ExportValidationError):
            runner.run()

        self.assertEqual(events, ["validate"])

    def test_opt_out_failure_aborts_before_campaign(self) -> None:
        events: list[str] = []

        def sync_opt_outs() -> None:
            events.append("sync")
            raise RuntimeError("Twilio unavailable")

        runner = ScheduledCampaignRunner(
            config=_Config(dry_run=True),
            export_path=Path("ignored.xlsx"),
            validator=_Validator(events),
            sync_opt_outs=sync_opt_outs,
            campaign_manager=_CampaignManager(events),
            lock_path=Path("/tmp/ignored.lock"),
        )

        with self.assertRaisesRegex(RuntimeError, "Twilio unavailable"):
            runner.run()

        self.assertEqual(events, ["validate", "sync"])

    def test_successful_dry_run_validates_syncs_and_processes_campaign(self) -> None:
        events: list[str] = []
        with TemporaryDirectory() as temp_dir:
            runner = ScheduledCampaignRunner(
                config=_Config(dry_run=True),
                export_path=Path("ignored.xlsx"),
                validator=_Validator(events),
                sync_opt_outs=lambda: events.append("sync"),
                campaign_manager=_CampaignManager(events),
                lock_path=Path(temp_dir) / "campaign.lock",
            )

            result = runner.run()

        self.assertEqual(events, ["validate", "sync", "campaign"])
        self.assertEqual(result.export.row_count, 2)
        self.assertEqual(result.campaign["total_sent"], 2)

    def test_validated_export_is_synced_to_the_local_customer_master_before_campaigns(self) -> None:
        events: list[str] = []
        store = _CustomerStore(events)
        with TemporaryDirectory() as temp_dir:
            runner = ScheduledCampaignRunner(
                config=_Config(dry_run=True),
                export_path=Path("ignored.xlsx"),
                validator=_Validator(events),
                sync_opt_outs=lambda: events.append("sync"),
                campaign_manager=_CampaignManager(events),
                lock_path=Path(temp_dir) / "campaign.lock",
                customer_store=store,
                customer_dataframe_loader=lambda _: pd.DataFrame(
                    [{"Mobile": "5550000001", "First Name": "Ana"}]
                ),
            )

            result = runner.run()

        self.assertEqual(events, ["validate", "customer_db", "sync", "campaign"])
        self.assertEqual(store.rows[0]["First Name"], "Ana")
        self.assertEqual(result.customer_sync["inserted"], 1)

    def test_existing_lock_prevents_an_overlapping_run(self) -> None:
        events: list[str] = []
        with TemporaryDirectory() as temp_dir:
            lock_path = Path(temp_dir) / "campaign.lock"
            runner = ScheduledCampaignRunner(
                config=_Config(dry_run=True),
                export_path=Path("ignored.xlsx"),
                validator=_Validator(events),
                sync_opt_outs=lambda: events.append("sync"),
                campaign_manager=_CampaignManager(events),
                lock_path=lock_path,
            )

            with RunLock(lock_path):
                with self.assertRaises(RunAlreadyInProgress):
                    runner.run()

        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
