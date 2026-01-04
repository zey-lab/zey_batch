"""Customer data model and merger."""

from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from dateutil import parser


class CustomerDataMerger:
    """Merge and manage customer list data."""

    def __init__(self, phone_column: str, config: dict):
        """Initialize customer data merger.

        Args:
            phone_column: Name of the phone number column
            config: Configuration dictionary with column mappings
        """
        self.phone_column = phone_column
        self.config = config

        # Get column names from config
        self.last_sms_sent_col = config.get('last_sms_sent_date', 'last_sms_sent_date')
        self.last_sms_status_col = config.get('last_sms_status', 'last_sms_status')
        self.opt_out_col = config.get('sms_opt_out', 'SMS_Opt_Out')
        self.opt_out_date_col = config.get('opt_out_date', 'Opt_Out_Date')
        self.last_review_sent_col = config.get('last_review_sent_date', 'last_review_sent_date')

    def merge_customer_lists(
        self,
        old_df: Optional[pd.DataFrame],
        new_df: pd.DataFrame
    ) -> pd.DataFrame:
        """Merge old and new customer lists.

        The merge logic:
        1. Use phone number as the unique key
        2. Keep SMS-related columns (last_sms_sent_date, last_sms_status, opt_out) from old list
        3. Override all other columns with data from new list
        4. Add any new customers from new list

        Args:
            old_df: Old customer list DataFrame (can be None)
            new_df: New customer list DataFrame

        Returns:
            Merged DataFrame
        """
        # If no old data, return new data with SMS columns initialized
        if old_df is None or old_df.empty:
            merged_df = new_df.copy()
            if self.last_sms_sent_col not in merged_df.columns:
                merged_df[self.last_sms_sent_col] = pd.NaT
            if self.last_sms_status_col not in merged_df.columns:
                merged_df[self.last_sms_status_col] = None
            if self.last_review_sent_col not in merged_df.columns:
                merged_df[self.last_review_sent_col] = pd.NaT
            return merged_df

        # Ensure phone column exists in both dataframes
        if self.phone_column not in old_df.columns:
            raise ValueError(f"Phone column '{self.phone_column}' not found in old customer list")
        if self.phone_column not in new_df.columns:
            raise ValueError(f"Phone column '{self.phone_column}' not found in new customer list")

        # Set phone number as index for both dataframes to align them
        old_df_indexed = old_df.set_index(self.phone_column)
        new_df_indexed = new_df.set_index(self.phone_column)

        # Combine new data with old data
        # combine_first updates null elements in new_df_indexed with value in the same location in old_df_indexed
        # It also includes rows from old_df_indexed that are not in new_df_indexed
        merged_df = new_df_indexed.combine_first(old_df_indexed)

        # CRITICAL: Enforce Opt-Out persistence
        # If a customer was opted out in the old list, they MUST remain opted out
        # regardless of what the new list says (unless we manually cleared it, but let's be safe)
        if self.opt_out_col in old_df_indexed.columns:
            # Find all phone numbers that were opted out in the old list
            # We check for 'yes', 'y', 'true' (case insensitive)
            old_opt_outs = old_df_indexed[
                old_df_indexed[self.opt_out_col].astype(str).str.lower().isin(['yes', 'y', 'true'])
            ].index

            # Force these to be 'Yes' in the merged dataframe
            if not old_opt_outs.empty:
                # Ensure column exists
                if self.opt_out_col not in merged_df.columns:
                    merged_df[self.opt_out_col] = None
                
                merged_df.loc[old_opt_outs, self.opt_out_col] = 'Yes'
                
                # Also preserve the date if it exists
                if self.opt_out_date_col in old_df_indexed.columns:
                    old_dates = old_df_indexed.loc[old_opt_outs, self.opt_out_date_col]
                    if self.opt_out_date_col not in merged_df.columns:
                        merged_df[self.opt_out_date_col] = None
                    merged_df.loc[old_opt_outs, self.opt_out_date_col] = old_dates

        # Ensure index name is preserved before resetting
        merged_df.index.name = self.phone_column

        # Reset index to make phone number a column again
        merged_df = merged_df.reset_index()

        # Ensure SMS columns exist and are initialized if they were missing in both
        if self.last_sms_sent_col not in merged_df.columns:
            merged_df[self.last_sms_sent_col] = pd.NaT
        if self.last_sms_status_col not in merged_df.columns:
            merged_df[self.last_sms_status_col] = None

        return merged_df

    @staticmethod
    def normalize_single_phone(phone: any) -> Optional[str]:
        """Normalize a single phone number string.
        
        Args:
            phone: Phone number input (string, int, float)
            
        Returns:
            Normalized phone string (E.164 format) or None if invalid
        """
        if pd.isna(phone) or str(phone).lower() == 'nan':
            return None
            
        # Convert to string and remove decimal point if it's a float-like string
        phone_str = str(phone).strip()
        if phone_str.endswith('.0'):
            phone_str = phone_str[:-2]
            
        # Remove common separators
        clean_phone = ''.join(c for c in phone_str if c.isalnum() or c == '+')
        
        if not clean_phone:
            return None

        if not clean_phone.startswith('+'):
            # Assume US number if no country code
            if len(clean_phone) == 10:
                clean_phone = f'+1{clean_phone}'
            elif len(clean_phone) == 11 and clean_phone.startswith('1'):
                clean_phone = f'+{clean_phone}'
            else:
                # If it's not 10 or 11 digits, we might just prepend + and hope for the best,
                # or it might be invalid. For now, let's prepend + if it looks like a full number
                clean_phone = f'+{clean_phone}'
                
        return clean_phone

    def normalize_phone_numbers(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize phone numbers to a standard format.

        Args:
            df: DataFrame with phone numbers

        Returns:
            DataFrame with normalized phone numbers
        """
        if self.phone_column not in df.columns:
            return df

        df = df.copy()

        # Use the static method for normalization
        df[self.phone_column] = df[self.phone_column].apply(self.normalize_single_phone)

        # Remove rows with invalid phone numbers
        df = df[df[self.phone_column].notna()]

        return df

    def deduplicate(self, df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
        """Remove duplicate phone numbers, keeping the first occurrence.

        Args:
            df: DataFrame to deduplicate

        Returns:
            Tuple of (deduplicated DataFrame, number of duplicates removed)
        """
        if self.phone_column not in df.columns:
            return df, 0

        original_count = len(df)
        df = df.drop_duplicates(subset=[self.phone_column], keep='first')
        duplicates_removed = original_count - len(df)

        return df, duplicates_removed

    def parse_date_column(self, df: pd.DataFrame, column: str) -> pd.DataFrame:
        """Parse date column to datetime format.

        Args:
            df: DataFrame
            column: Column name to parse

        Returns:
            DataFrame with parsed date column
        """
        if column not in df.columns:
            return df

        df = df.copy()

        def safe_parse_date(date_val):
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

        df[column] = df[column].apply(safe_parse_date)

        return df

    def filter_opted_out(self, df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
        """Filter out customers who have opted out of SMS.

        Args:
            df: Customer DataFrame

        Returns:
            Tuple of (filtered DataFrame without opted-out customers, count of filtered customers)
        """
        if self.opt_out_col not in df.columns:
            # No opt-out column, return all customers
            return df, 0

        original_count = len(df)

        def is_opted_out(value):
            """Check if customer has opted out."""
            if pd.isna(value):
                return False  # Empty = not opted out

            value_str = str(value).strip().upper()

            # Check for various "yes" values
            opt_out_values = ['YES', 'Y', 'TRUE', '1', 'STOP', 'UNSUBSCRIBE', 'OPT-OUT', 'OPTOUT']

            return value_str in opt_out_values

        # Keep only customers who have NOT opted out
        df_filtered = df[~df[self.opt_out_col].apply(is_opted_out)].copy()

        filtered_count = original_count - len(df_filtered)

        return df_filtered, filtered_count

    def filter_by_last_sms_date(
        self,
        df: pd.DataFrame,
        days_threshold: int,
        exclude_empty: bool = True
    ) -> pd.DataFrame:
        """Filter customers based on last SMS sent date.

        Args:
            df: Customer DataFrame
            days_threshold: Only include customers where last SMS was sent more than this many days ago
            exclude_empty: If True, include customers with no SMS history

        Returns:
            Filtered DataFrame
        """
        if self.last_sms_sent_col not in df.columns:
            return df

        df = self.parse_date_column(df, self.last_sms_sent_col)

        now = datetime.now()
        threshold_date = now - timedelta(days=days_threshold)

        def should_include(last_sms_date):
            if pd.isna(last_sms_date):
                return exclude_empty
            return last_sms_date <= threshold_date

        return df[df[self.last_sms_sent_col].apply(should_include)].copy()
