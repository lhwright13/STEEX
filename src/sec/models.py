"""Data models for SEC filings."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class InsiderTransaction:
    """A single insider transaction from Form 4."""

    ticker: str
    company_name: str
    company_cik: str
    insider_name: str
    insider_cik: str
    is_director: bool
    is_officer: bool
    is_ten_percent_owner: bool
    officer_title: str
    transaction_date: str
    transaction_code: str  # P=purchase, S=sale, A=award, etc.
    acquired_disposed: str  # A=acquired, D=disposed
    shares: float
    price_per_share: float
    total_value: float
    shares_owned_after: float
    filing_date: str
    filing_url: str

    @property
    def is_purchase(self) -> bool:
        """Check if this is an open market purchase."""
        return (
            self.transaction_code in ("P", "J")
            and self.acquired_disposed == "A"
            and self.shares > 0
            and self.price_per_share > 0
        )

    @property
    def role(self) -> str:
        """Get human-readable role description."""
        roles = []
        if self.is_officer:
            roles.append(self.officer_title if self.officer_title else "Officer")
        if self.is_director:
            roles.append("Director")
        if self.is_ten_percent_owner:
            roles.append("10% Owner")
        return ", ".join(roles) if roles else "Unknown"


@dataclass
class Filing:
    """Metadata for an SEC filing."""

    accession: str
    cik: str
    link: str
    filed_at: str
    file_path: Optional[str] = None
