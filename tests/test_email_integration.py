from __future__ import annotations

import base64
import json
from email import policy
from email.parser import BytesParser
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

from sms_campaign.data_store import ZeyDataStore
from sms_campaign.services.email_sender import EmailConfig, EmailSender, render_campaign_email
from sms_campaign.sqlite_campaigns import EmailCampaignRunner


class _Response:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


class TestEmailIntegration(unittest.TestCase):
  def test_dry_run_renders_branded_email_without_network_or_history(self):
    with self.subTest(mode="dry-run"):
      import tempfile
      tmp_path = Path(tempfile.mkdtemp())
      store = ZeyDataStore(Path(tmp_path) / "zey.sqlite3")
      store.sync_customers(pd.DataFrame([{
        "UserID": "V-1", "Mobile": "5550000001", "FirstName": "Ana",
        "EmailAddress": "ana@example.com", "LastVisited": "2025-01-01",
      }]))
      store.import_campaigns_from_dataframe(pd.DataFrame([{
        "Text/Prompt": "Hi {first_name}, we miss you!", "Type (Campaing / Reminder)": "Campaign",
        "Filter-Last Visit Days": 1, "Channels": "email", "Email Subject": "Hello {first_name}",
        "Approved": 1,
      }]))
      sender = EmailSender(EmailConfig(sender="zeybrowwax@gmail.com"), dry_run=True)
      runner = EmailCampaignRunner(store, sender, test_emails=["ana@example.com"])

      result = runner.run_campaign(runner.pending_campaigns()[0], campaign_id=1)

      self.assertTrue(result.dry_run)
      self.assertEqual(result.sent_count, 0)
      self.assertEqual(result.previews[0]["subject"], "Hello Ana")
      self.assertIn("Ana", result.previews[0]["text"])
      self.assertEqual(len(store.export_table("email_history")), 0)


  def test_live_send_uses_gmail_messages_send_and_logs_history(self):
    sender = EmailSender(
        EmailConfig(sender="zeybrowwax@gmail.com", access_token="access-token"),
        dry_run=False,
    )
    with patch("urllib.request.urlopen", return_value=_Response(b'{"id":"gmail-123"}')) as open_url:
        success, status, error = sender.send_email(
            "ana@example.com", "Welcome", "<p>Hello Ana</p>", text_body="Hello Ana"
        )

    self.assertEqual((success, status, error), (True, "sent", None))
    self.assertEqual(sender.last_message_id, "gmail-123")
    request = open_url.call_args.args[0]
    self.assertTrue(request.full_url.endswith("/users/me/messages/send"))
    self.assertEqual(request.get_header("Authorization"), "Bearer access-token")
    raw = json.loads(request.data)["raw"]
    message = BytesParser(policy=policy.default).parsebytes(base64.urlsafe_b64decode(raw))
    self.assertTrue(message["From"].endswith("<zeybrowwax@gmail.com>"))
    self.assertEqual(message["To"], "ana@example.com")
    self.assertIn("Hello Ana", message.get_body(preferencelist=("plain",)).get_content())


  def test_email_campaign_channel_does_not_enable_sms_by_default(self):
    import tempfile
    tmp_path = Path(tempfile.mkdtemp())
    store = ZeyDataStore(Path(tmp_path) / "zey.sqlite3")
    store.import_campaigns_from_dataframe(pd.DataFrame([{
        "Text/Prompt": "Email only", "Type (Campaing / Reminder)": "Campaign", "Channels": "email",
    }]))
    campaigns = store.load_campaigns()
    self.assertEqual(campaigns.iloc[0]["channels"], "email")
    subject, text, html = render_campaign_email(
        subject="Hi {{first name}}", html_body=None, text_prompt="Hello {{first name}}",
        customer={"first_name": "Ana"},
    )
    self.assertEqual(subject, "Hi Ana")
    self.assertEqual(text, "Hello Ana")
    self.assertIn("Hello Ana", html)
