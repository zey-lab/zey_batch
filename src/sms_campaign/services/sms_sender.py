"""Twilio SMS sender service."""

import time
from typing import Optional, Tuple

from twilio.rest import Client
from twilio.base.exceptions import TwilioRestException


class SMSSender:
    """Handle SMS sending via Twilio."""

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_phone: str,
        api_key: str = None,
        api_secret: str = None,
        dry_run: bool = False,
        rate_limit_delay: float = 1.0
    ):
        """Initialize SMS sender.

        Args:
            account_sid: Twilio account SID
            auth_token: Twilio auth token (optional if api_key/secret provided)
            from_phone: Twilio phone number to send from
            api_key: Twilio API Key SID
            api_secret: Twilio API Secret
            dry_run: If True, don't actually send SMS
            rate_limit_delay: Delay in seconds between SMS sends
        """
        self.from_phone = from_phone
        self.dry_run = dry_run
        self.rate_limit_delay = rate_limit_delay

        if not dry_run:
            if api_key and api_secret:
                # Authenticate with API Key
                self.client = Client(api_key, api_secret, account_sid)
            else:
                # Authenticate with Auth Token
                self.client = Client(account_sid, auth_token)
        else:
            self.client = None

        # Statistics
        self.sent_count = 0
        self.failed_count = 0
        self.total_cost = 0.0

    def send_sms(self, to_phone: str, message: str) -> Tuple[bool, str, Optional[str]]:
        """Send an SMS message.

        Args:
            to_phone: Recipient phone number
            message: Message to send

        Returns:
            Tuple of (success: bool, status: str, error_message: Optional[str])
        """
        if self.dry_run:
            # Simulate sending in dry-run mode
            time.sleep(0.1)  # Simulate network delay
            self.sent_count += 1
            return True, "sent", None

        try:
            # Send SMS via Twilio
            message_obj = self.client.messages.create(
                body=message,
                from_=self.from_phone,
                to=to_phone
            )

            # Apply rate limiting
            time.sleep(self.rate_limit_delay)

            # Refresh message status to get the latest update
            message_obj = message_obj.fetch()
            status = message_obj.status

            if status in ['sent', 'delivered', 'queued']:
                self.sent_count += 1

                # Estimate cost (roughly $0.0075 per SMS in US)
                # This is an approximation - actual cost varies by destination
                self.total_cost += 0.0075

                return True, status, None
            else:
                self.failed_count += 1
                return False, status, f"Message status: {status}"

        except TwilioRestException as e:
            self.failed_count += 1
            error_msg = f"Twilio error: {e.msg}"
            return False, "failed", error_msg

        except Exception as e:
            self.failed_count += 1
            error_msg = f"Unexpected error: {str(e)}"
            return False, "failed", error_msg

    def get_statistics(self) -> dict:
        """Get sending statistics.

        Returns:
            Dictionary with statistics
        """
        total = self.sent_count + self.failed_count
        success_rate = (self.sent_count / total * 100) if total > 0 else 0

        return {
            'total_sent': self.sent_count,
            'total_failed': self.failed_count,
            'total_messages': total,
            'success_rate': f"{success_rate:.1f}%",
            'estimated_cost': f"${self.total_cost:.2f}",
            'dry_run': self.dry_run,
        }

    def reset_statistics(self):
        """Reset statistics counters."""
        self.sent_count = 0
        self.failed_count = 0
        self.total_cost = 0.0
