"""CLI command to sync opt-outs."""

import argparse
import sys
from pathlib import Path

from sms_campaign.utils.config import Config
from sms_campaign.utils.logger import CampaignLogger
from sms_campaign.services.opt_out_sync import OptOutSyncer
from sms_campaign.utils.file_handler import FileHandler
from sms_campaign.models.customer import CustomerDataMerger

def sync_opt_outs():
    """Sync opt-outs from Twilio to customer list."""
    logger = CampaignLogger()
    config = Config()
    
    logger.section("Starting Opt-Out Sync")
    
    # Validate config
    is_valid, errors = config.validate()
    if not is_valid:
        for error in errors:
            logger.error(error)
        sys.exit(1)

    if config.dry_run:
        logger.warning("DRY RUN MODE: Opt-out sync will be skipped or simulated")
        return

    try:
        # Initialize services
        file_handler = FileHandler()
        opt_out_syncer = OptOutSyncer(config, logger)
        
        # Load current customer list (Old list is the master)
        if not config.customers_old_path.exists():
            logger.warning(f"No existing customer list found at {config.customers_old_path}")
            return

        logger.info(f"Loading customer list: {config.customers_old_path.name}")
        df = file_handler.read_dataframe(config.customers_old_path)
        original_count = len(df)
        
        # Fetch consent changes (opt-outs and opt-ins)
        consent_updates = opt_out_syncer.fetch_consent_changes()
        
        opt_outs = consent_updates.get('opt_out', [])
        opt_ins = consent_updates.get('opt_in', [])
        
        if not opt_outs and not opt_ins:
            logger.info("No new opt-outs or opt-ins found.")
            return

        # Update list
        logger.info(f"Processing {len(opt_outs)} opt-outs and {len(opt_ins)} opt-ins...")
        updated_df = opt_out_syncer.update_customer_list(df, consent_updates)
        
        # Save back if changes were made
        # We check if any values changed
        opt_out_col = config.get_yaml('customer_columns', 'sms_opt_out', default='SMS_Opt_Out')
        
        if opt_out_col in updated_df.columns:
            # Calculate changes
            old_opt_out_count = len(df[df[opt_out_col].astype(str).str.lower().isin(['yes', 'y', 'true'])]) if opt_out_col in df.columns else 0
            new_opt_out_count = len(updated_df[updated_df[opt_out_col].astype(str).str.lower().isin(['yes', 'y', 'true'])])
            
            diff = new_opt_out_count - old_opt_out_count
            
            if diff != 0 or len(opt_ins) > 0:
                if diff > 0:
                    logger.success(f"Net change: +{diff} customers opted out")
                elif diff < 0:
                    logger.success(f"Net change: {abs(diff)} customers opted back in")
                
                file_handler.write_dataframe(updated_df, config.customers_old_path)
                logger.success(f"Saved updated list to {config.customers_old_path.name}")
            else:
                logger.info("No effective changes in customer list.")
        
    except Exception as e:
        logger.error(f"Opt-out sync failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    sync_opt_outs()
