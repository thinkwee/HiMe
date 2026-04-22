"""
Base data reader interface for flexible dataset support.
Defines the contract that all dataset readers must implement.
"""
from abc import ABC, abstractmethod
from pathlib import Path

import pandas as pd


class BaseDataReader(ABC):
    """
    Abstract base class for dataset readers.
    All dataset-specific readers should inherit from this class.
    """

    def __init__(self, data_path: Path):
        """
        Initialize data reader.

        Args:
            data_path: Path to dataset directory
        """
        self.data_path = Path(data_path)
        if not self.data_path.exists():
            raise ValueError(f"Data path does not exist: {data_path}")

    @abstractmethod
    def get_available_users(self, datasets: list[str] | None = None) -> list[str]:
        """
        Get list of all available user IDs.

        Args:
            datasets: Optional list of dataset names to filter by

        Returns:
            Sorted list of unique user IDs
        """

    @abstractmethod
    def load_feature_data(
        self,
        pids: list[str],
        feature_type: str,
        datasets: list[str] | None = None
    ) -> pd.DataFrame:
        """
        Load feature data for specified users.

        Args:
            pids: List of user IDs
            feature_type: Type of features to load
            datasets: Optional list of datasets to load from

        Returns:
            DataFrame with combined feature data
            Must contain at minimum: 'pid', 'date' columns
        """

    @abstractmethod
    def get_feature_types(self) -> list[str]:
        """
        Get list of available feature types.

        Returns:
            List of feature type names
        """

    @abstractmethod
    def get_feature_columns(self, feature_type: str) -> list[str]:
        """
        Get list of available feature columns for a feature type.

        Args:
            feature_type: Type of features

        Returns:
            List of column names (excluding 'pid', 'date')
        """

    @abstractmethod
    def get_date_range(self, pids: list[str]) -> tuple:
        """
        Get the date range for specified users.

        Args:
            pids: List of user IDs

        Returns:
            Tuple of (min_date, max_date)
        """

    def get_datasets(self) -> list[str]:
        """
        Get list of available datasets.
        Default implementation returns empty list.
        Override if your data source has multiple datasets.

        Returns:
            List of dataset names
        """
        return []

    def get_user_info(
        self,
        pids: list[str],
        datasets: list[str] | None = None
    ) -> pd.DataFrame:
        """
        Get user metadata/info.
        Optional method - default returns empty DataFrame.

        Args:
            pids: List of user IDs
            datasets: Optional list of datasets

        Returns:
            DataFrame with user info
        """
        return pd.DataFrame()

    def load_survey_data(
        self,
        pids: list[str],
        survey_type: str,
        datasets: list[str] | None = None
    ) -> pd.DataFrame:
        """
        Load survey data for specified users.
        Optional method - default returns empty DataFrame.
        Override if your data source has survey data.

        Args:
            pids: List of user IDs
            survey_type: Type of survey to load
            datasets: Optional list of datasets

        Returns:
            DataFrame with survey data
        """
        return pd.DataFrame()
