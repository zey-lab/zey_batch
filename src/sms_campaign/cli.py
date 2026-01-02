"""Command-line interface for SMS Campaign Manager."""

import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from sms_campaign.manager import CampaignManager
from sms_campaign.utils.config import Config
from sms_campaign.utils.logger import CampaignLogger

console = Console()


def print_banner():
    """Print application banner."""
    banner = """
    ╔═══════════════════════════════════════════════════════╗
    ║                                                       ║
    ║         SMS Campaign Management System                ║
    ║              Customer Engagement Tool                 ║
    ║                                                       ║
    ╚═══════════════════════════════════════════════════════╝
    """
    console.print(banner, style="bold cyan")


def display_file_status(files_info: dict, config: Config):
    """Display status of data files.

    Args:
        files_info: Dictionary with file information
        config: Configuration object
    """
    table = Table(title="Data Files Status", show_header=True, header_style="bold magenta")
    table.add_column("File", style="cyan", no_wrap=True)
    table.add_column("Status", style="yellow")
    table.add_column("Last Modified", style="green")
    table.add_column("Size", style="blue")

    # Customers old file
    old_info = files_info['customers_old']
    if old_info:
        table.add_row(
            old_info['name'],
            "✓ Found",
            old_info['modified_str'],
            f"{old_info['size_mb']} MB"
        )
    else:
        table.add_row(
            config.customers_list_old,
            "✗ Not Found",
            "N/A",
            "N/A"
        )

    # Customers new file
    new_info = files_info['customers_new']
    if new_info:
        table.add_row(
            new_info['name'],
            "✓ Found",
            new_info['modified_str'],
            f"{new_info['size_mb']} MB"
        )
    else:
        table.add_row(
            config.customers_list_new,
            "✗ Not Found (REQUIRED)",
            "N/A",
            "N/A"
        )

    # Campaign config file
    campaign_info = files_info['campaigns']
    if campaign_info:
        table.add_row(
            campaign_info['name'],
            "✓ Found",
            campaign_info['modified_str'],
            f"{campaign_info['size_mb']} MB"
        )
    else:
        table.add_row(
            config.campaign_config,
            "✗ Not Found (REQUIRED)",
            "N/A",
            "N/A"
        )

    console.print(table)
    console.print()


def validate_files(files_info: dict) -> tuple[bool, list[str]]:
    """Validate that required files exist.

    Args:
        files_info: Dictionary with file information

    Returns:
        Tuple of (is_valid, list of errors)
    """
    errors = []

    if not files_info['customers_new']:
        errors.append(f"Required file not found: customers_list_new")

    if not files_info['campaigns']:
        errors.append(f"Required file not found: campaigns configuration")

    return len(errors) == 0, errors


def show_configuration(config: Config):
    """Display current configuration.

    Args:
        config: Configuration object
    """
    console.print("\n[bold cyan]Configuration:[/bold cyan]")

    config_data = {
        "Data Folder": str(config.data_folder),
        "Logs Folder": str(config.logs_folder),
        "Twilio Phone": config.twilio_phone_number if config.twilio_phone_number else "Not configured",
        "Dry Run Mode": "Yes" if config.dry_run else "No",
        "SMS Rate Limit": f"{config.sms_rate_limit_delay}s",
    }

    # Add test phone numbers if configured
    test_numbers = config.test_phone_numbers
    if test_numbers:
        config_data["Test Phone Numbers"] = f"{len(test_numbers)} configured"

    for key, value in config_data.items():
        console.print(f"  {key}: [green]{value}[/green]")

    # Show test numbers detail if configured
    if test_numbers:
        console.print("\n[bold yellow]⚠ TEST MODE ACTIVE[/bold yellow]")
        console.print("[yellow]Only these numbers will receive SMS:[/yellow]")
        for num in test_numbers:
            console.print(f"  • {num}")

    console.print()


def confirm_execution(config: Config) -> bool:
    """Ask user to confirm execution.

    Args:
        config: Configuration object

    Returns:
        True if user confirms, False otherwise
    """
    console.print()

    # Get test numbers for display
    test_numbers = config.test_phone_numbers

    if config.dry_run:
        console.print(Panel(
            "[yellow]DRY RUN MODE ENABLED[/yellow]\n\n"
            "SMS messages will NOT be sent. This is a simulation.\n"
            "To send real messages, set DRY_RUN=false in your .env file.",
            title="Dry Run Mode",
            border_style="yellow"
        ))
    else:
        # Build warning message
        warning_msg = "[red bold]LIVE MODE - REAL SMS WILL BE SENT[/red bold]\n\n"

        if test_numbers:
            warning_msg += f"[yellow]TEST MODE: Only {len(test_numbers)} phone numbers will receive SMS.[/yellow]\n"
            warning_msg += "[yellow]Test numbers configured in .env[/yellow]\n\n"
        else:
            warning_msg += "This will send real SMS messages to ALL eligible customers.\n"

        warning_msg += "Please ensure you have reviewed the campaign configuration."

        console.print(Panel(
            warning_msg,
            title="Live Mode Warning",
            border_style="red" if not test_numbers else "yellow"
        ))

    console.print()

    # Ask for confirmation
    return Confirm.ask(
        "[bold yellow]Do you want to proceed with the campaign execution?[/bold yellow]",
        default=False
    )


def main():
    """Main CLI entry point."""
    try:
        # Print banner
        print_banner()

        # Load configuration
        console.print("[cyan]Loading configuration...[/cyan]")
        config = Config()

        # Create directories if they don't exist
        config.create_directories()

        # Validate configuration
        is_valid, errors = config.validate()
        if not is_valid:
            console.print("\n[red bold]Configuration Errors:[/red bold]")
            for error in errors:
                console.print(f"  [red]✗ {error}[/red]")
            console.print("\n[yellow]Please check your .env file and config.yml[/yellow]")
            sys.exit(1)

        # Show configuration
        show_configuration(config)

        # Initialize logger
        logger = CampaignLogger(log_dir=config.logs_folder)
        logger.info("SMS Campaign Manager started")

        # Initialize campaign manager
        manager = CampaignManager(config, logger)

        # Check file status
        console.print("[cyan]Checking data files...[/cyan]\n")
        files_info = manager.check_files()
        display_file_status(files_info, config)

        # Validate files
        files_valid, file_errors = validate_files(files_info)
        if not files_valid:
            console.print("[red bold]Missing Required Files:[/red bold]")
            for error in file_errors:
                console.print(f"  [red]✗ {error}[/red]")
            console.print(f"\n[yellow]Please add the required files to: {config.data_folder}[/yellow]")
            sys.exit(1)

        # Warn if customers_list_new is not recent
        new_info = files_info['customers_new']
        if new_info:
            console.print(Panel(
                f"[bold]Customer List Last Modified:[/bold]\n\n"
                f"{new_info['modified_str']}\n\n"
                f"[yellow]Please ensure this is the latest customer list before proceeding.[/yellow]\n"
                f"If you need to update the file, press 'n' to cancel and update the file.",
                title="Data Freshness Check",
                border_style="yellow"
            ))
            console.print()

            if not Confirm.ask("[bold]Is this the latest customer list?[/bold]", default=True):
                console.print("\n[yellow]Please update the customer list file and run the application again.[/yellow]")
                sys.exit(0)

        # Confirm execution
        if not confirm_execution(config):
            console.print("\n[yellow]Campaign execution cancelled by user.[/yellow]")
            sys.exit(0)

        # Run campaign
        console.print("\n[bold green]Starting campaign execution...[/bold green]\n")

        summary = manager.run()

        # Final success message
        if summary['success']:
            console.print("\n")
            console.print(Panel(
                f"[bold green]Campaign Execution Completed Successfully![/bold green]\n\n"
                f"Campaigns Processed: {summary['campaigns_processed']}\n"
                f"Total SMS Sent: {summary['total_sent']}\n"
                f"Total Failed: {summary['total_failed']}\n"
                f"Duration: {summary['duration']}\n"
                f"\nLog file: {logger.log_file}",
                title="Success",
                border_style="green"
            ))
        else:
            console.print("\n[red]Campaign execution completed with errors. Check logs for details.[/red]")

    except KeyboardInterrupt:
        console.print("\n\n[yellow]Campaign execution interrupted by user.[/yellow]")
        sys.exit(130)

    except Exception as e:
        console.print(f"\n[red bold]Fatal Error:[/red bold] {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
