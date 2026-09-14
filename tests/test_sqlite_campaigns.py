from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import pandas as pd

from sms_campaign.data_store import ZeyDataStore
from sms_campaign.services.sms_sender import SMSSender
from sms_campaign.sqlite_campaigns import SQLiteCampaignRunner


class TestSQLiteCampaignRunner(unittest.TestCase):
    def _store(self, temp_dir: str) -> ZeyDataStore:
        store = ZeyDataStore(Path(temp_dir) / "zey.sqlite3")
        store.sync_customers(pd.DataFrame([
            {"UserID": "V-1", "Mobile": "5550000001", "FirstName": "Ana", "LastVisited": "2025-01-01"},
            {"UserID": "V-2", "Mobile": "5550000002", "FirstName": "Opted"},
        ]))
        store.set_opt_out("+15550000002")
        store.import_campaigns_from_dataframe(pd.DataFrame([
            {
                "Text/Prompt": "Hi {first_name}, welcome!",
                "SMS Text Character Limit": 160,
                "Type (Campaing / Reminder)": "Campaign",
                "Filter-Last Visit Days": 30,
                "Filter-Last SMS Day": 15,
                "Rank": 1,
            }
        ]))
        return store

    def test_dry_run_uses_sqlite_rules_without_fake_history(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = self._store(temp_dir)
            sender = SMSSender("", "", "", dry_run=True)
            runner = SQLiteCampaignRunner(store, sender)
            campaign = runner.pending_campaigns()[0]
            result = runner.run_campaign(campaign, campaign_id=1)

            self.assertTrue(result.dry_run)
            self.assertEqual(result.eligible_count, 1)
            self.assertEqual(result.previews[0]["message"], "Hi Ana, welcome!")
            self.assertEqual(len(store.export_table("sms_history")), 0)

    def test_live_run_logs_success_and_twilio_sid(self) -> None:
        with TemporaryDirectory() as temp_dir:
            store = self._store(temp_dir)
            sender = Mock()
            sender.dry_run = False
            sender.last_message_sid = "SM-123"
            sender.send_sms.return_value = (True, "delivered", None)
            runner = SQLiteCampaignRunner(store, sender, test_phones=["5550000001"])
            result = runner.run_campaign(runner.pending_campaigns()[0], campaign_id=1)
            history = store.export_table("sms_history")

            self.assertEqual(result.sent_count, 1)
            self.assertEqual(history.iloc[0]["twilio_sid"], "SM-123")
            self.assertEqual(history.iloc[0]["status"], "delivered")


if __name__ == "__main__":
    unittest.main()
