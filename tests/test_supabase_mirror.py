from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import pandas as pd

from sms_campaign.supabase_mirror import SupabaseMirror, _json_safe


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class TestSupabaseMirror(unittest.TestCase):
    def test_json_safe_converts_nan_and_numpy_scalars(self) -> None:
        frame = pd.DataFrame([{"id": 1, "missing": None, "nan": float("nan")}])
        row = _json_safe(frame.to_dict("records")[0])
        self.assertEqual(row["id"], 1)
        self.assertIsNone(row["missing"])
        self.assertIsNone(row["nan"])
        json.dumps(row)

    @patch("sms_campaign.supabase_mirror.urllib.request.urlopen", return_value=_Response())
    def test_upsert_batches_rows_and_uses_server_headers(self, urlopen) -> None:
        mirror = SupabaseMirror("https://example.supabase.co", "secret", batch_size=2)
        count = mirror.mirror_table(
            "transactions", "transaction_id",
            pd.DataFrame([{"transaction_id": 1}, {"transaction_id": 2}, {"transaction_id": 3}]),
        )

        self.assertEqual(count, 3)
        self.assertEqual(urlopen.call_count, 2)
        first = urlopen.call_args_list[0].args[0]
        self.assertIn("on_conflict=transaction_id", first.full_url)
        self.assertEqual(first.get_header("Authorization"), "Bearer secret")

    @patch("sms_campaign.supabase_mirror.urllib.request.urlopen", return_value=_Response())
    def test_transaction_export_only_sends_relational_columns(self, urlopen) -> None:
        mirror = SupabaseMirror("https://example.supabase.co", "secret")
        mirror.mirror_table(
            "campaigns", "campaign_id",
            pd.DataFrame([{"campaign_id": 1, "rank": "15.0", "unexpected": "ignored"}]),
        )
        payload = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(payload[0]["rank"], 15)
        self.assertNotIn("unexpected", payload[0])


if __name__ == "__main__":
    unittest.main()
