"""Configuration management for SMS Campaign Manager."""

import os
from pathlib import Path
from typing import Any, Dict

import yaml
from dotenv import load_dotenv


class Config:
    """Configuration manager for the SMS Campaign system."""

    def __init__(self, env_file: Path = None, config_file: Path = None):
        """Initialize configuration.

        Args:
            env_file: Path to .env file
            config_file: Path to config.yml file
        """
        # Load environment variables
        if env_file and env_file.exists():
            load_dotenv(env_file)
        else:
            load_dotenv()  # Load from default .env if exists

        # Get project root
        self.project_root = Path.cwd()

        # Load YAML configuration
        if config_file is None:
            config_file = self.project_root / "config" / "config.yml"

        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                self.yaml_config = yaml.safe_load(f)
        else:
            self.yaml_config = {}

        # Initialize paths
        self._init_paths()

    def _init_paths(self):
        """Initialize all file paths from YAML config with .env overrides."""
        # Get from YAML first, then allow .env override
        self.data_folder = self.project_root / self.get_env(
            "DATA_FOLDER",
            self.get_yaml("paths", "data_folder", default="data")
        )
        self.config_folder = self.project_root / self.get_env(
            "CONFIG_FOLDER",
            self.get_yaml("paths", "config_folder", default="config")
        )
        self.logs_folder = self.project_root / self.get_env(
            "LOGS_FOLDER",
            self.get_yaml("paths", "logs_folder", default="logs")
        )
        self.archive_folder = self.project_root / self.get_env(
            "ARCHIVE_FOLDER",
            self.get_yaml("paths", "archive_folder", default="data/archive")
        )
        self.delete_temp_folder = self.project_root / self.get_env(
            "DELETE_TEMP_FOLDER",
            self.get_yaml("paths", "delete_temp_folder", default="data/delete_temp")
        )

        # File names - YAML first, then .env override
        self.customers_list_old = self.get_env(
            "CUSTOMERS_LIST_OLD",
            self.get_yaml("files", "customers_list_old", default="customers_list_old.xlsx")
        )
        self.customers_list_new = self.get_env(
            "CUSTOMERS_LIST_NEW",
            self.get_yaml("files", "customers_list_new", default="customers_list_new.xlsx")
        )
        self.campaign_config = self.get_env(
            "CAMPAIGN_CONFIG",
            self.get_yaml("files", "campaign_config", default="campaigns.xlsx")
        )

        # Full file paths
        self.customers_old_path = self.data_folder / self.customers_list_old
        self.customers_new_path = self.data_folder / self.customers_list_new
        self.campaign_config_path = self.data_folder / self.campaign_config

    def get_env(self, key: str, default: Any = None) -> Any:
        """Get environment variable with default value.

        Args:
            key: Environment variable key
            default: Default value if key not found

        Returns:
            Environment variable value or default
        """
        value = os.getenv(key, default)

        if isinstance(value, str):
            # Strip whitespace from string values
            value = value.strip()
            
            # Convert string boolean values
            if value.lower() == 'true':
                return True
            elif value.lower() == 'false':
                return False

        return value

    def get_yaml(self, *keys, default: Any = None) -> Any:
        """Get value from YAML config using nested keys.

        Args:
            *keys: Nested keys to traverse
            default: Default value if key path not found

        Returns:
            Value from YAML config or default
        """
        result = self.yaml_config
        for key in keys:
            if isinstance(result, dict) and key in result:
                result = result[key]
            else:
                return default
        return result

    @property
    def twilio_account_sid(self) -> str:
        """Get Twilio account SID."""
        return self.get_env("TWILIO_ACCOUNT_SID", "")

    @property
    def twilio_auth_token(self) -> str:
        """Get Twilio auth token."""
        return self.get_env("TWILIO_AUTH_TOKEN", "")

    @property
    def twilio_api_key(self) -> str:
        """Get Twilio API Key SID."""
        return self.get_env("TWILIO_API_KEY", "")

    @property
    def twilio_api_secret(self) -> str:
        """Get Twilio API Secret."""
        return self.get_env("TWILIO_API_SECRET", "")

    @property
    def twilio_phone_number(self) -> str:
        """Get Twilio phone number."""
        return self.get_env("TWILIO_PHONE_NUMBER", "")

    @property
    def phone_number_column(self) -> str:
        """Get phone number column name."""
        # .env overrides YAML
        return self.get_env(
            "PHONE_NUMBER_COLUMN",
            self.get_yaml("phone", "column_name", default="phone_number")
        )

    @property
    def dry_run(self) -> bool:
        """Check if running in dry-run mode."""
        # .env overrides YAML
        env_value = os.getenv("DRY_RUN")
        if env_value is not None:
            return str(env_value).lower() == 'true'

        # Fall back to YAML
        return self.get_yaml("sms", "dry_run", default=False)

    @property
    def sms_rate_limit_delay(self) -> float:
        """Get SMS rate limit delay in seconds."""
        # .env overrides YAML
        delay = self.get_env(
            "SMS_RATE_LIMIT_DELAY",
            self.get_yaml("sms", "rate_limit_delay", default=1.0)
        )
        return float(delay)

    @property
    def log_level(self) -> str:
        """Get logging level."""
        return self.get_env("LOG_LEVEL", "INFO")

    @property
    def test_phone_numbers(self) -> list[str]:
        """Get test phone numbers for filtering.

        Returns:
            List of phone numbers to restrict sending to (empty list = no restriction)
        """
        # .env overrides YAML
        numbers_str = os.getenv("TEST_PHONE_NUMBERS")

        if numbers_str is not None:
            # Use .env value
            if not numbers_str or numbers_str.strip() == "":
                return []
            # Split by comma and clean up
            numbers = [n.strip() for n in numbers_str.split(",") if n.strip()]
            return numbers

        # Fall back to YAML (deprecated, but kept for backward compatibility if key exists)
        yaml_numbers = self.get_yaml("test_mode", "phone_numbers", default=[])
        if isinstance(yaml_numbers, list):
            return [str(n).strip() for n in yaml_numbers if n]
        return []

    def get_customer_columns(self) -> Dict[str, str]:
        """Get customer column mappings."""
        return self.get_yaml("customer_columns", default={})

    def get_campaign_columns(self) -> Dict[str, str]:
        """Get campaign column mappings."""
        return self.get_yaml("campaign_columns", default={})

    def validate(self) -> tuple[bool, list[str]]:
        """Validate configuration.

        Returns:
            Tuple of (is_valid, list of error messages)
        """
        errors = []

        # Validate Twilio credentials if not in dry-run mode
        if not self.dry_run:
            if not self.twilio_account_sid:
                errors.append("TWILIO_ACCOUNT_SID is not set")
            if not self.twilio_auth_token:
                errors.append("TWILIO_AUTH_TOKEN is not set")
            if not self.twilio_phone_number:
                errors.append("TWILIO_PHONE_NUMBER is not set")

        # Validate required folders exist
        required_folders = [
            self.data_folder,
            self.config_folder,
            self.logs_folder,
        ]

        for folder in required_folders:
            if not folder.exists():
                errors.append(f"Required folder does not exist: {folder}")

        return len(errors) == 0, errors

    def create_directories(self):
        """Create all required directories if they don't exist."""
        directories = [
            self.data_folder,
            self.config_folder,
            self.logs_folder,
            self.archive_folder,
            self.delete_temp_folder,
        ]

        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
