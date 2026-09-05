from __future__ import annotations

import pandas as pd

from scripts.sync_campaigns import load_campaign_file, sync_campaign_file
from sms_campaign.data_store import ZeyDataStore


def _campaign_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Text/Prompt": "We miss you, {{First Name}}! Book your next visit.",
                "SMS Text Character Limit": 160,
                "Type (Campaing / Reminder)": "Campaign",
                "Filter-Last Visit Days": 60,
                "Filter-Last SMS Day": 30,
                "Rank": 1,
                "Campaign Process Date": "",
                "Campaign Process Status": "pending",
            }
        ]
    )


def test_campaign_file_import_is_idempotent(tmp_path):
    source = tmp_path / "campaigns.csv"
    _campaign_frame().to_csv(source, index=False)
    database = tmp_path / "campaigns.sqlite3"

    first = sync_campaign_file(source, database)
    second = sync_campaign_file(source, database)

    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert second["updated"] == 1
    campaigns = ZeyDataStore(database).export_table("campaigns")
    assert len(campaigns) == 1
    assert campaigns.iloc[0]["campaign_type"] == "Campaign"


def test_campaign_file_loader_rejects_missing_file(tmp_path):
    missing = tmp_path / "missing.xlsx"
    try:
        load_campaign_file(missing)
    except FileNotFoundError as error:
        assert "campaigns.xlsx" in str(error)
    else:
        raise AssertionError("missing campaign source should fail clearly")
