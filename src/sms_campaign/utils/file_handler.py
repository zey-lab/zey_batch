"""File handling utilities for customer and campaign data."""

import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd


class FileHandler:
    """Handle file operations for customer lists and campaign configurations."""

    @staticmethod
    def detect_file_format(file_path: Path) -> str:
        """Detect file format from extension.

        Args:
            file_path: Path to file

        Returns:
            File format ('xlsx' or 'csv')
        """
        suffix = file_path.suffix.lower()
        if suffix in ['.xlsx', '.xls']:
            return 'xlsx'
        elif suffix == '.csv':
            return 'csv'
        else:
            raise ValueError(f"Unsupported file format: {suffix}")

    @staticmethod
    def read_dataframe(file_path: Path) -> pd.DataFrame:
        """Read a dataframe from CSV or Excel file.

        Args:
            file_path: Path to the file

        Returns:
            Pandas DataFrame

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If file format is not supported
        """
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        file_format = FileHandler.detect_file_format(file_path)

        if file_format == 'xlsx':
            df = pd.read_excel(file_path, engine='openpyxl')
        elif file_format == 'csv':
            df = pd.read_csv(file_path)
        else:
            raise ValueError(f"Unsupported file format: {file_format}")

        return df

    @staticmethod
    def write_dataframe(df: pd.DataFrame, file_path: Path):
        """Write a dataframe to CSV or Excel file.

        Args:
            df: Pandas DataFrame to write
            file_path: Path to save the file
        """
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_format = FileHandler.detect_file_format(file_path)

        if file_format == 'xlsx':
            df.to_excel(file_path, index=False, engine='openpyxl')
        elif file_format == 'csv':
            df.to_csv(file_path, index=False)

    @staticmethod
    def get_file_info(file_path: Path) -> Optional[dict]:
        """Get file information including modification time and size.

        Args:
            file_path: Path to file

        Returns:
            Dictionary with file info or None if file doesn't exist
        """
        if not file_path.exists():
            return None

        stat = file_path.stat()
        mod_time = datetime.fromtimestamp(stat.st_mtime)

        return {
            'path': str(file_path),
            'name': file_path.name,
            'size': stat.st_size,
            'size_mb': round(stat.st_size / (1024 * 1024), 2),
            'modified': mod_time,
            'modified_str': mod_time.strftime('%Y-%m-%d %H:%M:%S'),
            'exists': True,
        }

    @staticmethod
    def move_file(source: Path, destination: Path, create_dirs: bool = True) -> bool:
        """Move a file from source to destination.

        Args:
            source: Source file path
            destination: Destination file path
            create_dirs: Create destination directory if it doesn't exist

        Returns:
            True if successful, False otherwise
        """
        if not source.exists():
            return False

        if create_dirs:
            destination.parent.mkdir(parents=True, exist_ok=True)

        shutil.move(str(source), str(destination))
        return True

    @staticmethod
    def copy_file(source: Path, destination: Path, create_dirs: bool = True) -> bool:
        """Copy a file from source to destination.

        Args:
            source: Source file path
            destination: Destination file path
            create_dirs: Create destination directory if it doesn't exist

        Returns:
            True if successful, False otherwise
        """
        if not source.exists():
            return False

        if create_dirs:
            destination.parent.mkdir(parents=True, exist_ok=True)

        shutil.copy2(str(source), str(destination))
        return True

    @staticmethod
    def backup_file(file_path: Path, backup_dir: Path) -> Optional[Path]:
        """Create a timestamped backup of a file.

        Args:
            file_path: File to backup
            backup_dir: Directory to store backup

        Returns:
            Path to backup file or None if source doesn't exist
        """
        if not file_path.exists():
            return None

        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_name = f"{file_path.stem}_{timestamp}{file_path.suffix}"
        backup_path = backup_dir / backup_name

        FileHandler.copy_file(file_path, backup_path)
        return backup_path

    @staticmethod
    def find_file_with_pattern(directory: Path, pattern: str) -> list[Path]:
        """Find files matching a pattern in a directory.

        Args:
            directory: Directory to search
            pattern: Glob pattern to match

        Returns:
            List of matching file paths
        """
        if not directory.exists():
            return []

        return list(directory.glob(pattern))
