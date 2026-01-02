"""Service to sync opt-outs from Twilio."""

from datetime import datetime
from typing import List, Dict, Any
import pandas as pd
from twilio.rest import Client
from sms_campaign.utils.config import Config
from sms_campaign.utils.logger import CampaignLogger
from sms_campaign.models.customer import CustomerDataMerger

class OptOutSyncer:
    """Syncs opt-out status from Twilio messages."""

    def __init__(self, config: Config, logger: CampaignLogger):
        self.config = config
        self.logger = logger
        
        # Initialize Twilio client
        if config.twilio_api_key and config.twilio_api_secret:
            self.client = Client(config.twilio_api_key, config.twilio_api_secret, config.twilio_account_sid)
        else:
            self.client = Client(config.twilio_account_sid, config.twilio_auth_token)

    def fetch_consent_changes(self, days_back: int = 30) -> Dict[str, List[str]]:
        """Fetch phone numbers that have replied with STOP or START keywords.
        
        Args:
            days_back: How many days back to check for messages
            
        Returns:
            Dictionary with 'opt_out' and 'opt_in' lists of phone numbers
        """
        self.logger.info(f"Fetching messages from last {days_back} days...")
        
        opt_out_keywords = ['stop', 'unsubscribe', 'cancel', 'end', 'quit']
        opt_in_keywords = ['start', 'yes', 'unstop']
        
        consent_changes = {} # phone -> 'opt_out' or 'opt_in'
        
        try:
            # Get messages sent TO our number (incoming messages)
            # Twilio returns messages in reverse chronological order (newest first)
            messages = self.client.messages.list(
                to=self.config.twilio_phone_number,
                date_sent_after=datetime.now() - pd.Timedelta(days=days_back)
            )
            
            for msg in messages:
                phone = msg.from_
                # If we already found a newer message for this number, skip older ones
                if phone in consent_changes:
                    continue
                    
                body = str(msg.body).strip().lower()
                
                if body in opt_out_keywords:
                    consent_changes[phone] = 'opt_out'
                    self.logger.info(f"Found opt-out from: {phone}")
                elif body in opt_in_keywords:
                    consent_changes[phone] = 'opt_in'
                    self.logger.info(f"Found opt-in from: {phone}")
            
            return {
                'opt_out': [k for k, v in consent_changes.items() if v == 'opt_out'],
                'opt_in': [k for k, v in consent_changes.items() if v == 'opt_in']
            }
            
        except Exception as e:
            self.logger.error(f"Failed to fetch messages: {e}")
            return {'opt_out': [], 'opt_in': []}

    def update_customer_list(self, customer_df: pd.DataFrame, consent_updates: Dict[str, List[str]]) -> pd.DataFrame:
        """Update customer dataframe with opt-out/opt-in status.
        
        Args:
            customer_df: Current customer DataFrame
            consent_updates: Dictionary with 'opt_out' and 'opt_in' lists
            
        Returns:
            Updated DataFrame
        """
        opted_out_numbers = consent_updates.get('opt_out', [])
        opted_in_numbers = consent_updates.get('opt_in', [])
        
        if not opted_out_numbers and not opted_in_numbers:
            return customer_df
            
        df = customer_df.copy()
        phone_col = self.config.phone_number_column
        opt_out_col = self.config.get_yaml('customer_columns', 'sms_opt_out', default='SMS_Opt_Out')
        opt_out_date_col = self.config.get_yaml('customer_columns', 'opt_out_date', default='Opt_Out_Date')
        
        # Ensure columns exist
        if opt_out_col not in df.columns:
            df[opt_out_col] = 'No'
        if opt_out_date_col not in df.columns:
            df[opt_out_date_col] = None
            
        # --- Process Opt-Outs ---
        if opted_out_numbers:
            normalized_opt_outs = []
            for num in opted_out_numbers:
                norm = CustomerDataMerger.normalize_single_phone(num)
                if norm:
                    normalized_opt_outs.append(norm)
                    
            mask_out = df[phone_col].apply(CustomerDataMerger.normalize_single_phone).isin(normalized_opt_outs)
            
            if mask_out.any():
                count = mask_out.sum()
                self.logger.warning(f"Marking {count} customers as opted out")
                
                df.loc[mask_out, opt_out_col] = 'Yes'
                
                # Update date only if not already set
                date_mask = mask_out & (df[opt_out_date_col].isna() | (df[opt_out_date_col] == ''))
                df.loc[date_mask, opt_out_date_col] = datetime.now().strftime('%Y-%m-%d')

        # --- Process Opt-Ins ---
        if opted_in_numbers:
            normalized_opt_ins = []
            for num in opted_in_numbers:
                norm = CustomerDataMerger.normalize_single_phone(num)
                if norm:
                    normalized_opt_ins.append(norm)
            
            mask_in = df[phone_col].apply(CustomerDataMerger.normalize_single_phone).isin(normalized_opt_ins)
            
            if mask_in.any():
                count = mask_in.sum()
                self.logger.success(f"Marking {count} customers as opted in (re-subscribed)")
                
                df.loc[mask_in, opt_out_col] = 'No'
                # Clear the opt-out date
                df.loc[mask_in, opt_out_date_col] = None

        return df
