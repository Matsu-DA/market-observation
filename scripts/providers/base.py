from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class MarketDataProvider(ABC):
    name: str

    @abstractmethod
    def fetch(self, dataset: str, start: date, end: date) -> pd.DataFrame:
        """Return canonical-schema DataFrame.

        - observed_date column is datetime.date (market/session label, not UTC)
        - source/dataset/ingested_at are NOT added here; storage layer fills them
        - Empty DataFrame is valid (holiday, not-yet-published)
        """
        raise NotImplementedError
