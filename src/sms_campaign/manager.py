"""Main campaign manager that orchestrates the SMS campaign workflow."""

from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from sms_campaign.models.campaign import Campaign, CampaignProcessor
from sms_campaign.models.customer import CustomerDataMerger
from sms_campaign.services.sms_sender import SMSSender
from sms_campaign.services.opt_out_sync import OptOutSyncer
from sms_campaign.utils.config import Config
from sms_campaign.utils.file_handler import FileHandler
from sms_campaign.utils.logger import CampaignLogger
from sms_campaign.utils.message_analyzer import MessageAnalyzer


class CampaignManager:
    """Main campaign manager."""

    def __init__(self, config: Config, logger: CampaignLogger):
        """Initialize campaign manager.

        Args:
            config: Configuration object
            logger: Logger instance
        """
        self.config = config
        self.logger = logger
        self.file_handler = FileHandler()

        # Initialize components
        customer_columns = config.get_customer_columns()
        campaign_columns = config.get_campaign_columns()

        self.customer_merger = CustomerDataMerger(
            phone_column=config.phone_number_column,
            config=customer_columns
        )

        self.campaign_processor = CampaignProcessor(
            column_config=campaign_columns,
            customer_columns=customer_columns
        )

        self.sms_sender = SMSSender(
            account_sid=config.twilio_account_sid,
            auth_token=config.twilio_auth_token,
            from_phone=config.twilio_phone_number,
            api_key=config.twilio_api_key,
            api_secret=config.twilio_api_secret,
            dry_run=config.dry_run,
            rate_limit_delay=config.sms_rate_limit_delay
        )

        self.message_analyzer = MessageAnalyzer()

        # State
        self.merged_customers_df: Optional[pd.DataFrame] = None
        self.campaigns_df: Optional[pd.DataFrame] = None

    def check_files(self) -> dict:
        """Check status of required files.

        Returns:
            Dictionary with file information
        """
        files_info = {
            'customers_old': self.file_handler.get_file_info(self.config.customers_old_path),
            'customers_new': self.file_handler.get_file_info(self.config.customers_new_path),
            'campaigns': self.file_handler.get_file_info(self.config.campaign_config_path),
        }

        return files_info

    def load_and_merge_customers(self) -> pd.DataFrame:
        """Load and merge customer lists.

        Returns:
            Merged customer DataFrame
        """
        self.logger.section("Loading Customer Data")

        # Load old customer list if exists
        old_df = None
        if self.config.customers_old_path.exists():
            self.logger.info(f"Loading old customer list: {self.config.customers_old_path.name}")
            old_df = self.file_handler.read_dataframe(self.config.customers_old_path)
            self.logger.stat("Old customers count", len(old_df))

        # Load new customer list
        if not self.config.customers_new_path.exists():
            raise FileNotFoundError(f"New customer list not found: {self.config.customers_new_path}")

        self.logger.info(f"Loading new customer list: {self.config.customers_new_path.name}")
        new_df = self.file_handler.read_dataframe(self.config.customers_new_path)
        self.logger.stat("New customers count", len(new_df))

        # Normalize phone numbers
        self.logger.info("Normalizing phone numbers...")
        new_df = self.customer_merger.normalize_phone_numbers(new_df)
        if old_df is not None:
            old_df = self.customer_merger.normalize_phone_numbers(old_df)

        # Merge customer lists
        self.logger.info("Merging customer lists...")
        merged_df = self.customer_merger.merge_customer_lists(old_df, new_df)

        # Deduplicate
        merged_df, duplicates = self.customer_merger.deduplicate(merged_df)
        if duplicates > 0:
            self.logger.warning(f"Removed {duplicates} duplicate phone numbers")

        # Note: We do NOT filter out opted-out customers here anymore.
        # We keep them in the dataframe so their status is preserved in the saved file.
        # The CampaignProcessor will filter them out before sending messages.
        
        self.logger.success(f"Active customer list: {len(merged_df)} customers")

        self.merged_customers_df = merged_df
        return merged_df

    def load_campaigns(self) -> list[Campaign]:
        """Load campaign configuration.

        Returns:
            List of Campaign objects
        """
        self.logger.section("Loading Campaign Configuration")

        if not self.config.campaign_config_path.exists():
            raise FileNotFoundError(f"Campaign config not found: {self.config.campaign_config_path}")

        self.logger.info(f"Loading campaigns from: {self.config.campaign_config_path.name}")
        self.campaigns_df = self.file_handler.read_dataframe(self.config.campaign_config_path)

        campaigns = self.campaign_processor.load_campaigns(self.campaigns_df)
        self.logger.stat("Total campaigns", len(campaigns))

        # Get pending campaigns
        # pending = self.campaign_processor.get_pending_campaigns(campaigns)
        # self.logger.stat("Pending campaigns", len(pending))

        return campaigns

    def process_campaign(self, campaign: Campaign) -> dict:
        """Process a single campaign.

        Args:
            campaign: Campaign to process

        Returns:
            Dictionary with processing results
        """
        self.logger.section(f"Processing Campaign {campaign.row_index + 1}")
        self.logger.info(f"Type: {campaign.campaign_type}")
        self.logger.info(f"Message template: {campaign.text_prompt[:100]}...")

        # Filter customers for this campaign
        self.logger.info("Filtering customers...")
        
        # Log initial count
        self.logger.stat("Initial customer pool", len(self.merged_customers_df))
        
        eligible_customers = self.campaign_processor.filter_customers_for_campaign(
            self.merged_customers_df,
            campaign
        )
        
        # Log count after campaign filters
        self.logger.stat("Customers after campaign filters", len(eligible_customers))

        # Apply test phone number filter if configured
        test_numbers = self.config.test_phone_numbers
        if test_numbers:
            # Normalize test numbers using the same logic as customer data
            normalized_test_numbers = []
            for num in test_numbers:
                norm_num = CustomerDataMerger.normalize_single_phone(num)
                if norm_num:
                    normalized_test_numbers.append(norm_num)

            phone_col = self.config.phone_number_column
            before_count = len(eligible_customers)
            eligible_customers = eligible_customers[
                eligible_customers[phone_col].isin(normalized_test_numbers)
            ]
            after_count = len(eligible_customers)

            self.logger.warning(
                f"TEST MODE: Filtering to {len(test_numbers)} test phone numbers (Normalized: {normalized_test_numbers})"
            )
            self.logger.stat("Customers before test filter", before_count)
            self.logger.stat("Customers after test filter", after_count)
            
            if before_count > 0 and after_count == 0:
                self.logger.warning("All eligible customers were filtered out by the test number list.")
                self.logger.info("Please check if the test numbers match the customers in the list.")
                self.logger.info(f"Available customer numbers: {eligible_customers[phone_col].tolist() if not eligible_customers.empty else 'None'}")

        eligible_count = len(eligible_customers)
        self.logger.stat("Final eligible customers", eligible_count)

        if eligible_count == 0:
            self.logger.warning("No eligible customers for this campaign")
            return {
                'campaign_index': campaign.row_index,
                'eligible_count': 0,
                'sent_count': 0,
                'failed_count': 0,
                'status': 'completed',
            }

        # Analyze sample messages for cost estimation
        self._analyze_campaign_messages(campaign, eligible_customers)

        # Send SMS to each eligible customer
        self.logger.info(f"Sending SMS to {eligible_count} customers...")
        sent_count = 0
        failed_count = 0

        phone_col = self.config.phone_number_column
        sms_date_col = self.customer_merger.last_sms_sent_col
        sms_status_col = self.customer_merger.last_sms_status_col

        # Ensure columns are object type to avoid FutureWarning
        if self.merged_customers_df[sms_date_col].dtype == 'float64':
            self.merged_customers_df[sms_date_col] = self.merged_customers_df[sms_date_col].astype('object')
        if self.merged_customers_df[sms_status_col].dtype == 'float64':
            self.merged_customers_df[sms_status_col] = self.merged_customers_df[sms_status_col].astype('object')

        for idx, customer_row in eligible_customers.iterrows():
            phone = customer_row[phone_col]

            # Generate personalized message
            message = self.campaign_processor.generate_message(campaign, customer_row)

            # Send SMS
            success, status, error = self.sms_sender.send_sms(phone, message)

            # Update customer record in merged dataframe
            customer_idx = self.merged_customers_df[
                self.merged_customers_df[phone_col] == phone
            ].index[0]

            self.merged_customers_df.at[customer_idx, sms_date_col] = datetime.now()
            self.merged_customers_df.at[customer_idx, sms_status_col] = status

            if success:
                sent_count += 1
                if sent_count % 10 == 0:
                    self.logger.info(f"Progress: {sent_count}/{eligible_count} sent")
            else:
                failed_count += 1
                self.logger.error(f"Failed to send to {phone}: {error}")

        # Report results
        self.logger.success(f"Campaign completed: {sent_count} sent, {failed_count} failed")

        return {
            'campaign_index': campaign.row_index,
            'eligible_count': eligible_count,
            'sent_count': sent_count,
            'failed_count': failed_count,
            'status': 'completed',
        }

    def update_campaign_status(self, campaign: Campaign, status: str = 'completed'):
        """Update campaign status in the dataframe.

        Args:
            campaign: Campaign to update
            status: Status to set
        """
        date_col = self.config.get_yaml('campaign_columns', 'process_date', default='Campaign Process Date')
        status_col = self.config.get_yaml('campaign_columns', 'process_status', default='Campaign Process Status')

        # Ensure columns are object type to avoid FutureWarning
        if self.campaigns_df[date_col].dtype == 'float64':
            self.campaigns_df[date_col] = self.campaigns_df[date_col].astype('object')
        if self.campaigns_df[status_col].dtype == 'float64':
            self.campaigns_df[status_col] = self.campaigns_df[status_col].astype('object')

        self.campaigns_df.at[campaign.row_index, date_col] = datetime.now()
        self.campaigns_df.at[campaign.row_index, status_col] = status

    def save_updated_data(self):
        """Save updated customer and campaign data."""
        self.logger.section("Saving Updated Data")

        # Save updated campaign configuration
        self.logger.info(f"Saving campaign configuration...")
        self.file_handler.write_dataframe(self.campaigns_df, self.config.campaign_config_path)
        self.logger.success("Campaign configuration saved")

        # Save updated customer list as new "old" list
        self.logger.info(f"Saving updated customer list...")

        # First, move old file to delete_temp if it exists
        if self.config.customers_old_path.exists():
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            old_backup_name = f"{self.config.customers_old_path.stem}_{timestamp}{self.config.customers_old_path.suffix}"
            old_backup_path = self.config.delete_temp_folder / old_backup_name

            self.logger.info(f"Moving old customer list to: {old_backup_path.name}")
            self.file_handler.move_file(self.config.customers_old_path, old_backup_path)

        # Save merged customers as new old list
        self.file_handler.write_dataframe(self.merged_customers_df, self.config.customers_old_path)
        self.logger.success(f"Customer list saved: {self.config.customers_old_path.name}")

        # Move customers_new to delete_temp
        if self.config.customers_new_path.exists():
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            new_backup_name = f"{self.config.customers_new_path.stem}_{timestamp}{self.config.customers_new_path.suffix}"
            new_backup_path = self.config.delete_temp_folder / new_backup_name

            self.logger.info(f"Moving processed new customer list to: {new_backup_path.name}")
            self.file_handler.move_file(self.config.customers_new_path, new_backup_path)

        self.logger.success("All data saved successfully")

    def _analyze_campaign_messages(self, campaign: Campaign, eligible_customers: pd.DataFrame):
        """
        Analyze campaign messages for cost and encoding warnings.

        Args:
            campaign: Campaign to analyze
            eligible_customers: DataFrame of eligible customers
        """
        from rich.console import Console
        from rich.table import Table

        console = Console()

        # Sample up to 5 messages for analysis
        sample_size = min(5, len(eligible_customers))
        sample_customers = eligible_customers.head(sample_size)

        # Generate sample messages
        sample_messages = []
        for _, customer_row in sample_customers.iterrows():
            message = self.campaign_processor.generate_message(campaign, customer_row)
            sample_messages.append(message)

        # Analyze first message in detail
        first_analysis = self.message_analyzer.analyze_message(sample_messages[0])

        # Analyze entire campaign
        campaign_analysis = self.message_analyzer.analyze_campaign(
            [self.campaign_processor.generate_message(campaign, row)
             for _, row in eligible_customers.iterrows()]
        )

        # Display analysis
        self.logger.section("📊 Message Cost Analysis")

        # Create cost table
        cost_table = Table(title="Campaign Message Details", show_header=True)
        cost_table.add_column("Metric", style="cyan")
        cost_table.add_column("Value", style="yellow")

        cost_table.add_row("Message Length", f"{first_analysis['length']} characters")
        cost_table.add_row("Effective Length", f"{first_analysis['effective_length']} characters")
        cost_table.add_row("Encoding", first_analysis['encoding'])
        cost_table.add_row("Segments per Message", str(first_analysis['segments']))
        cost_table.add_row("Cost per Message", first_analysis['cost_formatted'])
        cost_table.add_row("Characters Remaining", f"{first_analysis['chars_remaining']} chars")

        cost_table.add_row("─" * 20, "─" * 20)  # Separator

        cost_table.add_row("Total Recipients", str(campaign_analysis['total_messages']))
        cost_table.add_row("Total Segments", str(campaign_analysis['total_segments']))
        cost_table.add_row("Total Campaign Cost", campaign_analysis['total_cost_formatted'])
        cost_table.add_row("Avg Message Length", f"{campaign_analysis['avg_length']} chars")
        cost_table.add_row("Avg Segments", campaign_analysis['avg_segments'])

        console.print(cost_table)

        # Display sample message
        console.print("\n[bold cyan]Sample Message:[/bold cyan]")
        console.print(f"[white]{sample_messages[0]}[/white]\n")

        # Display warnings
        if first_analysis['warnings']:
            console.print("[bold red]⚠️  WARNINGS:[/bold red]")
            for warning in first_analysis['warnings']:
                console.print(f"  {warning}")
            console.print()

        # Display recommendations
        if first_analysis['recommendations']:
            console.print("[bold yellow]💡 RECOMMENDATIONS:[/bold yellow]")
            for rec in first_analysis['recommendations']:
                console.print(f"  {rec}")
            console.print()

        # Show cost comparison if not optimal
        if not first_analysis['is_optimal']:
            optimal_cost = len(eligible_customers) * 0.0079  # 1 segment per message
            current_cost = float(campaign_analysis['total_cost'])
            savings = current_cost - optimal_cost
            savings_pct = (savings / current_cost) * 100 if current_cost > 0 else 0

            console.print(f"[bold red]💰 COST IMPACT:[/bold red]")
            console.print(f"  Current cost:  ${current_cost:.2f}")
            console.print(f"  Optimal cost:  ${optimal_cost:.2f}")
            console.print(f"  Extra cost:    ${savings:.2f} ({savings_pct:.0f}% more expensive)")
            console.print(f"  You could save ${savings:.2f} by optimizing this message!\n")

        # Show Unicode characters if any
        if first_analysis['unicode_characters']:
            console.print("[bold yellow]Unicode Characters Detected:[/bold yellow]")
            chars_str = ' '.join(first_analysis['unicode_characters'][:10])
            if len(first_analysis['unicode_characters']) > 10:
                chars_str += f" ... (+{len(first_analysis['unicode_characters'])-10} more)"
            console.print(f"  {chars_str}\n")

    def run(self) -> dict:
        """Run the complete campaign workflow.

        Returns:
            Dictionary with execution summary
        """
        start_time = datetime.now()

        try:
            # Load and merge customers
            self.load_and_merge_customers()

            # Load campaigns
            campaigns = self.load_campaigns()

            # Get pending campaigns
            # pending_campaigns = self.campaign_processor.get_pending_campaigns(campaigns)
            pending_campaigns = campaigns
            if not pending_campaigns:
                self.logger.warning("No pending campaigns to process")
                return {
                    'success': True,
                    'campaigns_processed': 0,
                    'total_sent': 0,
                    'total_failed': 0,
                    'duration': str(datetime.now() - start_time),
                }

            # Process each pending campaign
            results = []
            for campaign in pending_campaigns:
                self.logger.info(f"DEBUG: merged_customers_df size before campaign: {len(self.merged_customers_df)}")
                result = self.process_campaign(campaign)
                self.logger.info(f"DEBUG: merged_customers_df size after campaign: {len(self.merged_customers_df)}")
                results.append(result)

                # Update campaign status
                self.update_campaign_status(campaign)

                # Save after each campaign to ensure data integrity
                self.file_handler.write_dataframe(self.merged_customers_df, self.config.customers_old_path)
                self.logger.info(f"DEBUG: Saved dataframe with {len(self.merged_customers_df)} rows")
                self.file_handler.write_dataframe(self.campaigns_df, self.config.campaign_config_path)

            # Save final updated data
            self.save_updated_data()

            # Print final statistics
            self.logger.section("Execution Summary")

            total_sent = sum(r['sent_count'] for r in results)
            total_failed = sum(r['failed_count'] for r in results)

            sms_stats = self.sms_sender.get_statistics()

            summary = {
                'success': True,
                'campaigns_processed': len(pending_campaigns),
                'total_sent': total_sent,
                'total_failed': total_failed,
                'duration': str(datetime.now() - start_time),
                **sms_stats,
            }

            self.logger.print_table("Campaign Execution Summary", summary)

            return summary

        except Exception as e:
            self.logger.error(f"Campaign execution failed: {str(e)}")
            raise
