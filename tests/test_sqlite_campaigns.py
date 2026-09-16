from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

import pandas as pd

from sms_campaign.data_store import ZeyDataStore
from sms_campaign.models.campaign import Campaign, CampaignProcessor
from sms_campaign.services.sms_sender import SMSSender
from sms_campaign.sqlite_campaigns import SQLiteCampaignRunner, SQLITE_CAMPAIGN_COLUMNS, SQLITE_CUSTOMER_COLUMNS


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
                "Approved": 1,
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

    def test_unapproved_campaign_is_never_pending_regardless_of_type(self) -> None:
        """Regression test for the 2026-09-14 incident: a non-Announce
        campaign (type=Campaign) with approved=0 must never be eligible,
        even though older logic treated non-Announce types as always
        pending."""
        with TemporaryDirectory() as temp_dir:
            store = ZeyDataStore(Path(temp_dir) / "zey.sqlite3")
            store.sync_customers(pd.DataFrame([
                {"UserID": "V-1", "Mobile": "5550000001", "FirstName": "Ana", "LastVisited": "2020-01-01"},
            ]))
            store.import_campaigns_from_dataframe(pd.DataFrame([
                {
                    "Text/Prompt": "Miss you!", "Type (Campaing / Reminder)": "Campaign",
                    "Filter-Last Visit Days": 1, "Filter-Last SMS Day": 1,
                    # Approved intentionally omitted -> must default to 0/not-pending.
                }
            ]))
            sender = SMSSender("", "", "", dry_run=True)
            runner = SQLiteCampaignRunner(store, sender)
            self.assertEqual(runner.pending_campaigns(), [])

    def test_test_recipients_restricts_run_to_only_those_numbers(self) -> None:
        """A campaign-level test_recipients value must override normal
        customer filtering and send to ONLY the listed numbers."""
        with TemporaryDirectory() as temp_dir:
            store = ZeyDataStore(Path(temp_dir) / "zey.sqlite3")
            store.sync_customers(pd.DataFrame([
                {"UserID": "V-1", "Mobile": "5550000001", "FirstName": "Ana", "LastVisited": "2020-01-01"},
                {"UserID": "V-2", "Mobile": "5550000002", "FirstName": "Bo", "LastVisited": "2020-01-01"},
            ]))
            store.import_campaigns_from_dataframe(pd.DataFrame([
                {
                    "Text/Prompt": "Miss you!", "Type (Campaing / Reminder)": "Campaign",
                    "Filter-Last Visit Days": 1, "Filter-Last SMS Day": 1,
                    "Approved": 1, "Test Recipients": "5550000001",
                }
            ]))
            sender = SMSSender("", "", "", dry_run=True)
            runner = SQLiteCampaignRunner(store, sender)
            result = runner.run_campaign(runner.pending_campaigns()[0], campaign_id=1)
            self.assertEqual(result.eligible_count, 1)
            self.assertEqual(result.previews[0]["mobile"], "+15550000001")

    def test_generate_message_survives_a_backslash_in_a_customer_field(self) -> None:
        """Regression test for the 2026-09-15 incident: generate_message's
        #column_name replacement loop passed the raw field value straight
        into Pattern.sub() as a *replacement template*, so any customer
        field containing a backslash sequence Python doesn't recognize as
        a valid escape (e.g. raw_json, an address, a stray backslash-u) raised
        re.PatternError and took down the entire campaign run before a
        single message could send."""
        processor = CampaignProcessor(
            column_config=SQLITE_CAMPAIGN_COLUMNS, customer_columns=SQLITE_CUSTOMER_COLUMNS
        )
        campaign = Campaign(
            row_index=0,
            data={"text_prompt": "Hi {first_name}, thanks!", "campaign_type": "Campaign"},
            column_config=SQLITE_CAMPAIGN_COLUMNS,
        )
        customer_row = pd.Series({
            "mobile": "+15550001111",
            "first_name": "Ana",
            "raw_json": '{"path": "C:\\Users\\name"}',
        })
        message = processor.generate_message(campaign, customer_row)
        self.assertEqual(message, "Hi Ana, thanks!")


if __name__ == "__main__":
    unittest.main()
