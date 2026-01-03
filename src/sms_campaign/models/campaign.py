"""Campaign data model and processor."""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
from dateutil import parser


class Campaign:
    """Represents a single SMS campaign."""

    def __init__(self, row_index: int, data: Dict[str, Any], column_config: Dict[str, str]):
        """Initialize campaign from row data.

        Args:
            row_index: Row index in the campaign configuration
            data: Campaign data dictionary
            column_config: Column name mappings from config
        """
        self.row_index = row_index
        self.raw_data = data
        self.column_config = column_config

        # Extract campaign fields using column mappings
        self.text_prompt = self._get_field('text_prompt', '')
        self.character_limit = self._get_field('character_limit', None)
        self.campaign_type = self._get_field('campaign_type', 'Campaign')
        self.filter_last_visit_days = self._get_field('filter_last_visit_days', None)
        self.filter_last_sms_days = self._get_field('filter_last_sms_days', None)
        self.rank = self._get_field('rank', 999)  # Default to high number (low priority)
        self.process_date = self._get_field('process_date', None)
        self.process_status = self._get_field('process_status', None)

        # Parse numeric filters
        if self.rank is not None:
            try:
                self.rank = int(float(self.rank))
            except (ValueError, TypeError):
                self.rank = 999

        if self.filter_last_visit_days is not None:
            try:
                self.filter_last_visit_days = int(float(self.filter_last_visit_days))
            except (ValueError, TypeError):
                self.filter_last_visit_days = None

        if self.filter_last_sms_days is not None:
            try:
                self.filter_last_sms_days = int(float(self.filter_last_sms_days))
            except (ValueError, TypeError):
                self.filter_last_sms_days = None

        if self.character_limit is not None:
            try:
                self.character_limit = int(float(self.character_limit))
            except (ValueError, TypeError):
                self.character_limit = 160  # Default SMS length

    def _get_field(self, config_key: str, default: Any = None) -> Any:
        """Get field value using column mapping.

        Args:
            config_key: Key in column config
            default: Default value if not found

        Returns:
            Field value
        """
        column_name = self.column_config.get(config_key, config_key)
        value = self.raw_data.get(column_name, default)

        # Handle NaN values
        if pd.isna(value):
            return default

        return value

    def is_processed(self) -> bool:
        """Check if campaign has already been processed.

        Returns:
            True if campaign has been processed
        """
        # Campaign is considered processed if both date and status are set
        has_date = self.process_date is not None and not pd.isna(self.process_date)
        has_status = self.process_status is not None and not pd.isna(self.process_status) and str(self.process_status).strip() != ''

        return has_date and has_status

    def is_birthday_campaign(self) -> bool:
        """Check if this is a birthday campaign.

        Returns:
            True if campaign type is Birthday
        """
        if not self.campaign_type:
            return False
        return 'birthday' in str(self.campaign_type).lower()

    def is_anniversary_campaign(self) -> bool:
        """Check if this is an anniversary campaign.

        Returns:
            True if campaign type is Anniversary
        """
        if not self.campaign_type:
            return False
        return 'anniversary' in str(self.campaign_type).lower()

    def is_announce_campaign(self) -> bool:
        """Check if this is an announce campaign.

        Returns:
            True if campaign type is Announce
        """
        if not self.campaign_type:
            return False
        return 'announce' in str(self.campaign_type).lower()

    def __repr__(self) -> str:
        """String representation of campaign."""
        return f"Campaign(row={self.row_index}, type={self.campaign_type}, processed={self.is_processed()})"


class CampaignProcessor:
    """Process campaigns and filter customer data."""

    def __init__(self, column_config: Dict[str, str], customer_columns: Dict[str, str]):
        """Initialize campaign processor.

        Args:
            column_config: Campaign column mappings
            customer_columns: Customer column mappings
        """
        self.column_config = column_config
        self.customer_columns = customer_columns

        self.last_visit_col = customer_columns.get('last_visit_date', 'last_visit_date')
        self.last_sms_sent_col = customer_columns.get('last_sms_sent_date', 'last_sms_sent_date')
        self.birthday_col = customer_columns.get('birthday', 'birthday')
        self.customer_since_col = customer_columns.get('customer_since', 'customer_since')
        self.first_name_col = customer_columns.get('first_name', 'first_name')
        self.opt_out_col = customer_columns.get('sms_opt_out', 'SMS_Opt_Out')

    def load_campaigns(self, df: pd.DataFrame) -> List[Campaign]:
        """Load campaigns from DataFrame.

        Args:
            df: Campaign configuration DataFrame

        Returns:
            List of Campaign objects
        """
        campaigns = []

        for idx, row in df.iterrows():
            campaign = Campaign(idx, row.to_dict(), self.column_config)
            campaigns.append(campaign)

        return campaigns

    def get_pending_campaigns(self, campaigns: List[Campaign]) -> List[Campaign]:
        """Get campaigns that haven't been processed yet.

        Note: Only 'Announce' campaigns are filtered by processed status.
        Other campaigns (Campaign, Reminder, etc.) are always considered pending
        as they run continuously based on customer criteria.

        Args:
            campaigns: List of all campaigns

        Returns:
            List of unprocessed campaigns
        """
        return [
            c for c in campaigns 
            if not c.is_announce_campaign() or not c.is_processed()
        ]

    def filter_customers_for_campaign(
        self,
        customers_df: pd.DataFrame,
        campaign: Campaign
    ) -> pd.DataFrame:
        """Filter customers based on campaign criteria.

        Args:
            customers_df: Customer DataFrame
            campaign: Campaign object

        Returns:
            Filtered customer DataFrame
        """
        filtered_df = customers_df.copy()
        print(f"DEBUG: Starting with {len(filtered_df)} customers")

        # Filter out opted-out customers
        if self.opt_out_col in filtered_df.columns:
            # Normalize to string, lower case, strip
            opt_out_series = filtered_df[self.opt_out_col].astype(str).str.lower().str.strip()
            # Keep only those who have NOT opted out
            filtered_df = filtered_df[~opt_out_series.isin(['yes', 'y', 'true'])]
            print(f"DEBUG: After opt-out filter: {len(filtered_df)} customers")

        # Parse date columns if they exist
        if self.last_visit_col in filtered_df.columns:
            filtered_df = self._parse_date_column(filtered_df, self.last_visit_col)

        if self.last_sms_sent_col in filtered_df.columns:
            filtered_df = self._parse_date_column(filtered_df, self.last_sms_sent_col)

        if self.birthday_col in filtered_df.columns:
            filtered_df = self._parse_date_column(filtered_df, self.birthday_col)

        if self.customer_since_col in filtered_df.columns:
            filtered_df = self._parse_date_column(filtered_df, self.customer_since_col)

        # Apply last visit filter
        if campaign.filter_last_visit_days is not None and self.last_visit_col in filtered_df.columns:
            filtered_df = self._filter_by_last_visit(filtered_df, campaign.filter_last_visit_days)
            print(f"DEBUG: After last visit filter ({campaign.filter_last_visit_days} days): {len(filtered_df)} customers. Sample data:")
            print(filtered_df.head())

        # Apply last SMS filter (unless it's a birthday, anniversary, or announce campaign)
        if campaign.filter_last_sms_days is not None and self.last_sms_sent_col in filtered_df.columns:
            if not campaign.is_birthday_campaign() and not campaign.is_anniversary_campaign() and not campaign.is_announce_campaign():
                filtered_df = self._filter_by_last_sms(filtered_df, campaign.filter_last_sms_days)
                print(f"DEBUG: After last SMS filter ({campaign.filter_last_sms_days} days): {len(filtered_df)} customers. Sample data:")
                print(filtered_df.head())

        # For birthday campaigns, filter by birthday
        if campaign.is_birthday_campaign() and self.birthday_col in filtered_df.columns:
            # Use filter_last_sms_days as the offset (default to 0 if not set)
            # If set to 7, we look for birthdays 7 days from now (send 7 days before birthday)
            offset = campaign.filter_last_sms_days if campaign.filter_last_sms_days is not None else 0
            filtered_df = self._filter_by_birthday(filtered_df, days_offset=offset)
            print(f"DEBUG: After birthday filter (offset {offset} days): {len(filtered_df)} customers. Sample data:")
            print(filtered_df.head())

        # For anniversary campaigns, filter by customer anniversary
        if campaign.is_anniversary_campaign() and self.customer_since_col in filtered_df.columns:
            filtered_df = self._filter_by_anniversary(filtered_df)
            print(f"DEBUG: After anniversary filter: {len(filtered_df)} customers. Sample data:")
            print(filtered_df.head())

        return filtered_df

    def _parse_date_column(self, df: pd.DataFrame, column: str) -> pd.DataFrame:
        """Parse date column to datetime.

        Args:
            df: DataFrame
            column: Column name

        Returns:
            DataFrame with parsed dates
        """
        if column not in df.columns:
            return df

        df = df.copy()

        def safe_parse(date_val):
            if pd.isna(date_val):
                return pd.NaT
            if isinstance(date_val, (datetime, pd.Timestamp)):
                return pd.to_datetime(date_val)
            try:
                return pd.to_datetime(date_val)
            except:
                try:
                    return parser.parse(str(date_val))
                except:
                    return pd.NaT

        df[column] = df[column].apply(safe_parse)
        return df

    def _filter_by_last_visit(self, df: pd.DataFrame, days: int) -> pd.DataFrame:
        """Filter by last visit date.

        Only include customers who haven't visited in the last N days.

        Args:
            df: Customer DataFrame
            days: Number of days threshold

        Returns:
            Filtered DataFrame
        """
        now = datetime.now()
        threshold = now - timedelta(days=days)

        def should_include(last_visit):
            if pd.isna(last_visit):
                return True  # Include if no visit recorded
            return last_visit <= threshold

        return df[df[self.last_visit_col].apply(should_include)].copy()

    def _filter_by_last_sms(self, df: pd.DataFrame, days: int) -> pd.DataFrame:
        """Filter by last SMS sent date.

        Only include customers who haven't received SMS in the last N days.

        Args:
            df: Customer DataFrame
            days: Number of days threshold

        Returns:
            Filtered DataFrame
        """
        now = datetime.now()
        threshold = now - timedelta(days=days)

        def should_include(last_sms):
            if pd.isna(last_sms):
                return True  # Include if no SMS sent
            return last_sms <= threshold

        return df[df[self.last_sms_sent_col].apply(should_include)].copy()

    def _filter_by_birthday(self, df: pd.DataFrame, days_offset: int = 0) -> pd.DataFrame:
        """Filter customers whose birthday is today + offset.

        Args:
            df: Customer DataFrame
            days_offset: Number of days from now to check for birthday. 
                         If 7, checks if birthday is 7 days from now.

        Returns:
            Filtered DataFrame with birthday customers
        """
        target_date = datetime.now() + timedelta(days=days_offset)

        def is_birthday_target(birthday):
            if pd.isna(birthday):
                return False
            try:
                # Compare month and day only
                bday = pd.to_datetime(birthday)
                return bday.month == target_date.month and bday.day == target_date.day
            except:
                return False

        return df[df[self.birthday_col].apply(is_birthday_target)].copy()

    def _filter_by_anniversary(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filter customers whose membership anniversary is today.

        Args:
            df: Customer DataFrame

        Returns:
            Filtered DataFrame with anniversary customers
        """
        today = datetime.now()

        def is_anniversary_today(customer_since):
            if pd.isna(customer_since):
                return False
            try:
                # Compare month and day only (anniversary of joining date)
                join_date = pd.to_datetime(customer_since)
                return join_date.month == today.month and join_date.day == today.day
            except:
                return False

        return df[df[self.customer_since_col].apply(is_anniversary_today)].copy()

    def generate_message(self, campaign: Campaign, customer_row: pd.Series) -> str:
        """Generate personalized message for a customer.

        Args:
            campaign: Campaign object
            customer_row: Customer data as Series

        Returns:
            Personalized message string
        """
        message = campaign.text_prompt

        # Replace placeholders with customer data
        # Supports two formats:
        # 1. {column_key} - e.g., {first_name}, {last_name}
        # 2. #column_name - e.g., #first_name, #last_visit_date

        # First, replace {column_key} format using config mappings
        for col_key, col_name in self.customer_columns.items():
            if col_name in customer_row.index:
                value = customer_row[col_name]
                if not pd.isna(value):
                    # Handle date formatting
                    if isinstance(value, (datetime, pd.Timestamp)):
                        value = value.strftime('%B %d, %Y')

                    # Replace {column_key} format
                    placeholder_curly = f"{{{col_key}}}"
                    message = message.replace(placeholder_curly, str(value))

        # Second, replace #column_name format using actual column names
        for col_name in customer_row.index:
            value = customer_row[col_name]
            if not pd.isna(value):
                # Handle date formatting
                if isinstance(value, (datetime, pd.Timestamp)):
                    value = value.strftime('%B %d, %Y')

                # Replace #column_name format (case-insensitive)
                placeholder_hash = f"#{col_name}"
                # Also support variations like #First_Name, #first_name
                import re
                pattern = re.compile(re.escape(placeholder_hash), re.IGNORECASE)
                message = pattern.sub(str(value), message)

        # Apply character limit if specified
        if campaign.character_limit and len(message) > campaign.character_limit:
            message = message[:campaign.character_limit - 3] + "..."

        return message
