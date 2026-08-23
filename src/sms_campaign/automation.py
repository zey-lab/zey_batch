"""Safety controls and input validation for scheduled campaign runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

import fcntl

import pandas as pd

from sms_campaign.models.customer import CustomerDataMerger
from sms_campaign.utils.file_handler import FileHandler


class AutomationSafetyError(RuntimeError):
    """Raised when an unattended run is not safe to start."""


class ExportValidationError(ValueError):
    """Raised when a customer export cannot safely enter the pipeline."""


class RunAlreadyInProgress(RuntimeError):
    """Raised when a second scheduled run attempts to use the same lock."""


class SupportsDryRun(Protocol):
    """Minimal configuration contract used by automation safety checks."""

    @property
    def dry_run(self) -> bool: ...


@dataclass(frozen=True)
class ExportValidationReport:
    """Sanitized summary of a customer export validation result."""

    row_count: int
    valid_mobile_count: int


def ensure_dry_run(config: SupportsDryRun) -> None:
    """Reject unattended execution unless the application is in dry-run mode."""
    if not config.dry_run:
        raise AutomationSafetyError(
            "Scheduled automation is dry-run only until live sending is explicitly approved."
        )


class CustomerExportValidator:
    """Validate the minimum data contract required before campaign processing."""

    def __init__(
        self,
        mobile_column: str = "Mobile",
        minimum_rows: int | None = None,
        maximum_age_hours: float | None = None,
    ) -> None:
        self.mobile_column = mobile_column
        self.minimum_rows = minimum_rows
        self.maximum_age_hours = maximum_age_hours

    def validate(self, export_path: Path) -> ExportValidationReport:
        """Return a sanitized validation report or reject an unsafe export."""
        if not export_path.exists():
            raise ExportValidationError(f"Customer export was not found: {export_path.name}")

        if self.maximum_age_hours is not None:
            modified_at = datetime.fromtimestamp(export_path.stat().st_mtime, tz=timezone.utc)
            age_hours = (datetime.now(timezone.utc) - modified_at).total_seconds() / 3600
            if age_hours > self.maximum_age_hours:
                raise ExportValidationError(
                    "Customer export exceeds the configured maximum age"
                )

        try:
            customers = FileHandler.read_dataframe(export_path)
        except (OSError, ValueError, pd.errors.ParserError) as error:
            raise ExportValidationError(
                f"Customer export could not be read: {export_path.name}"
            ) from error

        if self.mobile_column not in customers.columns:
            raise ExportValidationError(
                f"Customer export is missing required column: {self.mobile_column}"
            )

        if customers.empty:
            raise ExportValidationError("Customer export contains no customer rows")

        if self.minimum_rows is not None and len(customers) < self.minimum_rows:
            raise ExportValidationError(
                "Customer export is below the configured minimum row count"
            )

        valid_mobile_count = sum(
            CustomerDataMerger.normalize_single_phone(value) is not None
            for value in customers[self.mobile_column]
        )
        if valid_mobile_count == 0:
            raise ExportValidationError("Customer export contains no usable mobile numbers")

        return ExportValidationReport(
            row_count=len(customers),
            valid_mobile_count=valid_mobile_count,
        )


class RunLock:
    """A non-blocking filesystem lock for a single scheduled pipeline run."""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = lock_path
        self._handle = None

    def __enter__(self) -> "RunLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.lock_path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            self._handle.close()
            self._handle = None
            raise RunAlreadyInProgress(
                "Another scheduled campaign run is already in progress"
            ) from error
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        if self._handle is not None:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            self._handle.close()
            self._handle = None


class SupportsCampaignRun(Protocol):
    """Minimal contract used by the scheduled runner."""

    def run(self) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ScheduledRunResult:
    """Sanitized result of one scheduled dry-run execution."""

    export: ExportValidationReport
    campaign: dict[str, Any]


class ScheduledCampaignRunner:
    """Run the fail-closed daily pipeline in explicitly dry-run mode."""

    def __init__(
        self,
        config: SupportsDryRun,
        export_path: Path,
        validator: CustomerExportValidator,
        sync_opt_outs: Callable[[], None],
        campaign_manager: SupportsCampaignRun,
        lock_path: Path,
    ) -> None:
        self.config = config
        self.export_path = export_path
        self.validator = validator
        self.sync_opt_outs = sync_opt_outs
        self.campaign_manager = campaign_manager
        self.lock_path = lock_path

    def run(self) -> ScheduledRunResult:
        """Validate, sync consent, and execute a single dry-run campaign pass."""
        ensure_dry_run(self.config)
        with RunLock(self.lock_path):
            export_report = self.validator.validate(self.export_path)
            self.sync_opt_outs()
            campaign_summary = self.campaign_manager.run()
        return ScheduledRunResult(export=export_report, campaign=campaign_summary)
