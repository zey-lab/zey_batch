"""Colorful logging utility for SMS Campaign Manager."""

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler
from rich.theme import Theme

# Custom theme for colorful logging
custom_theme = Theme({
    "info": "cyan",
    "warning": "yellow bold",
    "error": "red bold",
    "success": "green bold",
    "highlight": "magenta bold",
    "stat": "blue",
})

console = Console(theme=custom_theme)


class CampaignLogger:
    """Enhanced logger with colorful output and file logging."""

    def __init__(self, name: str = "sms_campaign", log_dir: Optional[Path] = None):
        """Initialize the campaign logger.

        Args:
            name: Logger name
            log_dir: Directory to save log files
        """
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        # Console handler with Rich
        console_handler = RichHandler(
            console=console,
            rich_tracebacks=True,
            tracebacks_show_locals=True,
            show_time=True,
            show_path=False,
        )
        console_handler.setLevel(logging.INFO)
        self.logger.addHandler(console_handler)

        # File handler
        if log_dir:
            log_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = log_dir / f"campaign_{timestamp}.log"

            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(logging.DEBUG)
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)

            self.log_file = log_file
        else:
            self.log_file = None

    def info(self, message: str):
        """Log info message."""
        self.logger.info(message)

    def success(self, message: str):
        """Log success message in green."""
        console.print(f"✓ {message}", style="success")
        self.logger.info(f"SUCCESS: {message}")

    def warning(self, message: str):
        """Log warning message in yellow."""
        console.print(f"⚠ {message}", style="warning")
        self.logger.warning(message)

    def error(self, message: str):
        """Log error message in red."""
        console.print(f"✗ {message}", style="error")
        self.logger.error(message)

    def highlight(self, message: str):
        """Log highlighted message in magenta."""
        console.print(message, style="highlight")
        self.logger.info(message)

    def stat(self, label: str, value: any):
        """Log a statistic in a formatted way."""
        console.print(f"  {label}: [stat]{value}[/stat]")
        self.logger.info(f"{label}: {value}")

    def section(self, title: str):
        """Print a section header."""
        console.rule(f"[bold cyan]{title}[/bold cyan]")
        self.logger.info(f"{'='*50}")
        self.logger.info(f"  {title}")
        self.logger.info(f"{'='*50}")

    def debug(self, message: str):
        """Log debug message."""
        self.logger.debug(message)

    def print_table(self, title: str, data: dict):
        """Print data in a nice table format."""
        from rich.table import Table

        table = Table(title=title, show_header=True, header_style="bold magenta")
        table.add_column("Field", style="cyan", no_wrap=True)
        table.add_column("Value", style="green")

        for key, value in data.items():
            table.add_row(str(key), str(value))

        console.print(table)

        # Also log to file
        self.logger.info(f"\n{title}")
        for key, value in data.items():
            self.logger.info(f"  {key}: {value}")
