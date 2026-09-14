"""Campaign execution against the unified SQLite customer master.

The older campaign manager is intentionally kept for the XLSX workflow.  This
module is the durable path for scheduled runs: it reads campaigns and
customers from SQLite, applies the same campaign rules, and records every
real Twilio result in ``sms_history``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd

from sms_campaign.data_store import ZeyDataStore
from sms_campaign.models.campaign import Campaign, CampaignProcessor
from sms_campaign.services.sms_sender import SMSSender
from sms_campaign.services.email_sender import EmailSender, render_campaign_email


SQLITE_CAMPAIGN_COLUMNS = {
    "text_prompt": "text_prompt",
    "character_limit": "character_limit",
    "campaign_type": "campaign_type",
    "filter_last_visit_days": "filter_last_visit_days",
    "filter_last_sms_days": "filter_last_sms_days",
    "rank": "rank",
    "process_date": "process_date",
    "process_status": "process_status",
    "channels": "channels",
    "email_subject": "email_subject",
    "email_html": "email_html",
}

SQLITE_CUSTOMER_COLUMNS = {
    "phone_number": "mobile",
    "last_sms_sent_date": "last_sms_sent_date",
    "last_visit_date": "last_visit",
    "birthday": "birthdate",
    "customer_since": "customer_since",
    "first_name": "first_name",
    "last_name": "last_name",
    "sms_opt_out": "sms_opt_out",
    "last_review_sent_date": "last_review_sent_date",
}


@dataclass(frozen=True)
class CampaignRunResult:
    """Sanitized summary of a campaign execution."""

    dry_run: bool
    campaign_id: int
    campaign_type: str
    eligible_count: int
    attempted_count: int
    sent_count: int
    failed_count: int
    skipped_count: int
    previews: tuple[dict[str, Any], ...] = ()


class SQLiteCampaignRunner:
    """Run ranked campaigns from SQLite with durable SMS history."""

    def __init__(
        self,
        store: ZeyDataStore,
        sender: SMSSender,
        *,
        test_phones: Iterable[str] = (),
        max_messages: int | None = None,
    ) -> None:
        self.store = store
        self.sender = sender
        self.test_phones = {
            store._normalize_phone(phone) for phone in test_phones
            if store._normalize_phone(phone)
        }
        self.max_messages = max_messages
        self.processor = CampaignProcessor(
            column_config=SQLITE_CAMPAIGN_COLUMNS,
            customer_columns=SQLITE_CUSTOMER_COLUMNS,
        )

    def load_customers(self) -> pd.DataFrame:
        """Load active customers and derive SMS/review history fields."""
        customers = self.store.get_active_customers()
        if customers.empty:
            return customers

        sms_history = self.store.export_table("sms_history")
        if sms_history.empty:
            customers["last_sms_sent_date"] = None
            customers["last_review_sent_date"] = None
        else:
            sms_history["sent_at"] = pd.to_datetime(sms_history["sent_at"], errors="coerce")
            latest_sms = sms_history.groupby("customer_id")["sent_at"].max()
            latest_review = (
                sms_history[sms_history["campaign_type"].str.lower().eq("review")]
                .groupby("customer_id")["sent_at"]
                .max()
            )
            customers["last_sms_sent_date"] = customers["customer_id"].map(latest_sms)
            customers["last_review_sent_date"] = customers["customer_id"].map(latest_review)

        # CampaignProcessor accepts yes/no style opt-out values.  SQLite stores
        # the normalized flag as an integer, so make the conversion explicit.
        customers["sms_opt_out"] = customers["sms_opt_out"].map(
            lambda value: "yes" if bool(value) else "no"
        )
        return customers

    def pending_campaigns(self) -> list[Campaign]:
        """Return active campaigns, excluding already processed announcements."""
        campaigns_df = self.store.load_campaigns()
        campaigns = self.processor.load_campaigns(campaigns_df)
        return sorted(
            [campaign for campaign in self.processor.get_pending_campaigns(campaigns) if campaign.supports_channel("sms")],
            key=lambda campaign: (campaign.rank, campaign.row_index),
        )

    def run_campaign(
        self,
        campaign: Campaign,
        *,
        campaign_id: int,
        preview_limit: int = 5,
    ) -> CampaignRunResult:
        """Evaluate and optionally send one SMS campaign."""
        customers = self.load_customers()
        eligible = self.processor.filter_customers_for_campaign(customers, campaign)
        if self.test_phones:
            eligible = eligible[eligible["mobile"].isin(self.test_phones)]

        previews = tuple(
            {
                "customer_id": int(row["customer_id"]),
                "mobile": row["mobile"],
                "message": self.processor.generate_message(campaign, row),
            }
            for _, row in eligible.head(preview_limit).iterrows()
        )
        if not campaign.is_announce_campaign() and not eligible.empty:
            history = self.store.export_table("sms_history")
            if not history.empty:
                today = pd.Timestamp.utcnow().date()
                sent_today = history[pd.to_datetime(history["sent_at"], errors="coerce").dt.date.eq(today)]
                eligible = eligible[~eligible["customer_id"].isin(sent_today["customer_id"])]
        if self.max_messages is not None:
            eligible = eligible.head(self.max_messages)
        if self.sender.dry_run:
            return CampaignRunResult(
                dry_run=True, campaign_id=campaign_id, campaign_type=str(campaign.campaign_type),
                eligible_count=len(eligible), attempted_count=0, sent_count=0, failed_count=0,
                skipped_count=0, previews=previews,
            )

        sent = failed = 0
        for _, row in eligible.iterrows():
            message = self.processor.generate_message(campaign, row)
            success, status, error = self.sender.send_sms(row["mobile"], message)
            self.store.log_sms(
                customer_id=int(row["customer_id"]), campaign_type=str(campaign.campaign_type),
                message_text=message, status=status,
                twilio_sid=getattr(self.sender, "last_message_sid", None),
                error_message=error, campaign_row=campaign_id,
            )
            if success:
                sent += 1
            else:
                failed += 1
        self.store.update_campaign_status(campaign_id, "completed")
        return CampaignRunResult(
            dry_run=False, campaign_id=campaign_id, campaign_type=str(campaign.campaign_type),
            eligible_count=len(eligible), attempted_count=len(eligible), sent_count=sent,
            failed_count=failed, skipped_count=0, previews=previews,
        )


class EmailCampaignRunner:
    """Run the same campaign rules for email with a separate audit trail."""

    def __init__(
        self,
        store: ZeyDataStore,
        sender: EmailSender,
        *,
        test_emails: Iterable[str] = (),
        max_messages: int | None = None,
    ) -> None:
        self.store = store
        self.sender = sender
        self.test_emails = {str(value).strip().lower() for value in test_emails if str(value).strip()}
        self.max_messages = max_messages
        self.processor = CampaignProcessor(
            column_config=SQLITE_CAMPAIGN_COLUMNS,
            customer_columns={
                **SQLITE_CUSTOMER_COLUMNS,
                "last_sms_sent_date": "last_email_sent_date",
                "sms_opt_out": "email_opt_out",
                "last_review_sent_date": "last_review_email_sent_date",
            },
        )

    def load_customers(self) -> pd.DataFrame:
        """Load customers and derive the independent email suppression fields."""
        customers = self.store.get_active_customers()
        if customers.empty:
            return customers
        history = self.store.export_table("email_history")
        if history.empty:
            customers["last_email_sent_date"] = None
            customers["last_review_email_sent_date"] = None
        else:
            history["sent_at"] = pd.to_datetime(history["sent_at"], errors="coerce")
            latest = history.groupby("customer_id")["sent_at"].max()
            latest_review = history[
                history["campaign_type"].astype(str).str.lower().eq("review")
            ].groupby("customer_id")["sent_at"].max()
            customers["last_email_sent_date"] = customers["customer_id"].map(latest)
            customers["last_review_email_sent_date"] = customers["customer_id"].map(latest_review)
        customers["email_opt_out"] = customers["email_opt_out"].map(
            lambda value: "yes" if bool(value) else "no"
        )
        return customers

    def pending_campaigns(self) -> list[Campaign]:
        campaigns = self.processor.load_campaigns(self.store.load_campaigns())
        return sorted(
            [campaign for campaign in self.processor.get_pending_campaigns(campaigns) if campaign.supports_channel("email")],
            key=lambda campaign: (campaign.rank, campaign.row_index),
        )

    def run_campaign(self, campaign: Campaign, *, campaign_id: int, preview_limit: int = 5) -> CampaignRunResult:
        customers = self.load_customers()
        eligible = self.processor.filter_customers_for_campaign(customers, campaign)
        if self.test_emails:
            eligible = eligible[eligible["email"].astype(str).str.lower().isin(self.test_emails)]
        previews: list[dict[str, Any]] = []
        for _, row in eligible.head(preview_limit).iterrows():
            subject, text_body, _ = render_campaign_email(
                subject=campaign.email_subject,
                html_body=campaign.email_html,
                text_prompt=campaign.text_prompt,
                customer=row.to_dict(),
            )
            previews.append({"customer_id": int(row["customer_id"]), "email": row["email"], "subject": subject, "text": text_body})

        if not campaign.is_announce_campaign() and not eligible.empty:
            history = self.store.export_table("email_history")
            if not history.empty:
                today = pd.Timestamp.utcnow().date()
                sent_today = history[pd.to_datetime(history["sent_at"], errors="coerce").dt.date.eq(today)]
                eligible = eligible[~eligible["customer_id"].isin(sent_today["customer_id"])]
        if self.max_messages is not None:
            eligible = eligible.head(self.max_messages)
        if self.sender.dry_run:
            return CampaignRunResult(
                dry_run=True, campaign_id=campaign_id, campaign_type=str(campaign.campaign_type),
                eligible_count=len(eligible), attempted_count=0, sent_count=0, failed_count=0,
                skipped_count=0, previews=tuple(previews),
            )

        sent = failed = 0
        for _, row in eligible.iterrows():
            subject, text_body, html_body = render_campaign_email(
                subject=campaign.email_subject,
                html_body=campaign.email_html,
                text_prompt=campaign.text_prompt,
                customer=row.to_dict(),
                logo_url=self.sender.config.logo_url,
            )
            success, status, error = self.sender.send_email(row["email"], subject, html_body, text_body=text_body)
            self.store.log_email(
                customer_id=int(row["customer_id"]), campaign_type=str(campaign.campaign_type),
                subject=subject, body=html_body, status=status, error_message=error, campaign_row=campaign_id,
            )
            if success:
                sent += 1
            else:
                failed += 1
        self.store.update_campaign_status(campaign_id, "completed")
        return CampaignRunResult(
            dry_run=False, campaign_id=campaign_id, campaign_type=str(campaign.campaign_type),
            eligible_count=len(eligible), attempted_count=len(eligible), sent_count=sent,
            failed_count=failed, skipped_count=0, previews=tuple(previews),
        )
