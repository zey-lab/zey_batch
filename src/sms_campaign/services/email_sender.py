"""Gmail API email sender with a safe dry-run default.

Email is intentionally separate from Twilio SMS.  Live mode uses the Gmail
API as the authenticated mailbox and requires a server-side OAuth refresh
token with the ``gmail.send`` scope.  No credential is accepted on the CLI.
"""

from __future__ import annotations

import base64
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from typing import Any


GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


@dataclass(frozen=True)
class EmailConfig:
    """Non-secret and secret email settings loaded from the environment."""

    sender: str
    sender_name: str = "Zey Brow & Wax"
    access_token: str = ""
    refresh_token: str = ""
    client_id: str = ""
    client_secret: str = ""
    logo_url: str = ""


class EmailSender:
    """Send branded email through Gmail, or preview it without sending."""

    def __init__(self, config: EmailConfig, *, dry_run: bool = True, timeout: int = 30):
        self.config = config
        self.dry_run = dry_run
        self.timeout = timeout
        self.last_message_id: str | None = None

    @classmethod
    def from_environment(cls, *, dry_run: bool = True) -> "EmailSender":
        return cls(
            EmailConfig(
                sender=os.getenv("EMAIL_FROM", "zeybrowwax@gmail.com").strip(),
                sender_name=os.getenv("EMAIL_FROM_NAME", "Zey Brow & Wax").strip(),
                access_token=os.getenv("GMAIL_ACCESS_TOKEN", "").strip(),
                refresh_token=os.getenv("GMAIL_REFRESH_TOKEN", "").strip(),
                client_id=os.getenv("GMAIL_CLIENT_ID", "").strip(),
                client_secret=os.getenv("GMAIL_CLIENT_SECRET", "").strip(),
                logo_url=os.getenv("EMAIL_LOGO_URL", "").strip(),
            ),
            dry_run=dry_run,
        )

    def validate_live_configuration(self) -> list[str]:
        """Return safe, actionable validation errors without exposing secrets."""
        errors: list[str] = []
        local_part, domain = parseaddr(self.config.sender)[1].rsplit("@", 1) if "@" in parseaddr(self.config.sender)[1] else ("", "")
        if not local_part or not domain:
            errors.append("EMAIL_FROM must be a valid mailbox address")
        if not self.config.access_token and not all(
            (self.config.refresh_token, self.config.client_id, self.config.client_secret)
        ):
            errors.append(
                "configure GMAIL_ACCESS_TOKEN or GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, and GMAIL_REFRESH_TOKEN"
            )
        return errors

    def send_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        *,
        text_body: str | None = None,
    ) -> tuple[bool, str, str | None]:
        """Send one email and return ``(success, status, error)``.

        Dry-run mode does not make a network request and does not create a
        history row; callers can use the returned preview separately.
        """
        recipient = parseaddr(to_email)[1].strip()
        if not self._valid_email(recipient):
            return False, "failed", "recipient email is invalid"
        if not subject.strip():
            return False, "failed", "subject is empty"
        if self.dry_run:
            return True, "dry_run", None

        try:
            token = self._access_token()
            message = self._mime_message(recipient, subject, html_body, text_body)
            payload = {"raw": base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")}
            request = urllib.request.Request(
                GMAIL_SEND_URL,
                data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
                method="POST",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response_data = json.loads(response.read().decode("utf-8") or "{}")
            self.last_message_id = response_data.get("id")
            return True, "sent", None
        except urllib.error.HTTPError as exc:
            # Never include Gmail's response body: it can contain addresses or
            # message data and may echo request details.
            self.last_message_id = None
            if exc.code in (401, 403):
                return False, "failed", "Gmail authorization rejected; verify gmail.send consent and mailbox access"
            return False, "failed", f"Gmail API HTTP {exc.code}"
        except urllib.error.URLError:
            self.last_message_id = None
            return False, "failed", "Gmail API connection failed"
        except (OSError, ValueError, RuntimeError) as exc:
            self.last_message_id = None
            return False, "failed", str(exc)

    def _access_token(self) -> str:
        if self.config.access_token:
            return self.config.access_token
        if not all((self.config.refresh_token, self.config.client_id, self.config.client_secret)):
            raise RuntimeError("Gmail OAuth refresh configuration is incomplete")
        request = urllib.request.Request(
            GOOGLE_TOKEN_URL,
            data=urllib.parse.urlencode(
                {
                    "client_id": self.config.client_id,
                    "client_secret": self.config.client_secret,
                    "refresh_token": self.config.refresh_token,
                    "grant_type": "refresh_token",
                }
            ).encode("ascii"),
            method="POST",
            headers={"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                token = json.loads(response.read().decode("utf-8")).get("access_token")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Google OAuth token request failed with HTTP {exc.code}") from exc
        if not token:
            raise RuntimeError("Google OAuth token response did not contain an access token")
        return token

    def _mime_message(
        self,
        recipient: str,
        subject: str,
        html_body: str,
        text_body: str | None,
    ) -> EmailMessage:
        message = EmailMessage()
        message["To"] = recipient
        message["From"] = formataddr((self.config.sender_name, self.config.sender))
        message["Subject"] = subject
        message.set_content(text_body or _html_to_text(html_body))
        message.add_alternative(html_body, subtype="html")
        return message

    @staticmethod
    def _valid_email(value: str) -> bool:
        return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value))


def render_campaign_email(
    *,
    subject: str,
    html_body: str | None,
    text_prompt: str,
    customer: dict[str, Any],
    logo_url: str = "",
) -> tuple[str, str, str]:
    """Render a campaign's subject/body using escaped customer values.

    If no HTML is supplied, a small responsive template with Zey Brow colors
    is used.  The logo URL is optional so a guessed website asset is never
    embedded; configure ``EMAIL_LOGO_URL`` after approving the exact asset.
    """
    values = {str(key).lower(): "" if value is None else html.escape(str(value)) for key, value in customer.items()}
    values.setdefault("first_name", values.get("first name", ""))
    values.setdefault("last_name", values.get("last name", ""))
    values.setdefault("email", "")

    def substitute(value: str) -> str:
        result = value
        for key, replacement in values.items():
            for token in (f"{{{key}}}", f"{{{{{key}}}}}", f"{{{{{key.replace('_', ' ')}}}}}", f"#{key}"):
                result = result.replace(token, replacement)
        return result

    rendered_subject = substitute(subject or "A message from Zey Brow & Wax")
    rendered_text = substitute(text_prompt)
    if html_body and html_body.strip():
        rendered_html = substitute(html_body)
    else:
        logo = f'<img src="{html.escape(logo_url)}" alt="Zey Brow &amp; Wax" style="max-width:180px;height:auto">' if logo_url else ""
        rendered_html = f"""<!doctype html><html><body style="margin:0;background:#f7f3f0;font-family:Arial,sans-serif;color:#302825">
<div style="max-width:600px;margin:24px auto;background:#fff;padding:32px;border-radius:14px">
{logo}<div style="height:4px;background:#bd8b74;margin:22px 0"></div>
<p style="font-size:17px;line-height:1.6">{rendered_text.replace(chr(10), '<br>')}</p>
<p style="color:#756762;font-size:13px">Zey Brow &amp; Wax · Dallas, TX</p>
</div></body></html>"""
    return rendered_subject, rendered_text, rendered_html


def _html_to_text(value: str) -> str:
    return re.sub(r"<[^>]+>", "", html.unescape(value)).replace("\u00a0", " ").strip()
