"""Insider buying scanner."""

from datetime import datetime
from typing import List, Optional

from ..client import EdgarClient
from ..models import InsiderTransaction
from ..parsers.form4 import parse_form4_xml


class InsiderScanner:
    """Scans SEC Form 4 filings for insider purchases."""

    def __init__(self, client: Optional[EdgarClient] = None):
        self.client = client or EdgarClient()

    def scan(
        self,
        days_back: int = 7,
        max_filings: int = 500,
        use_daily_index: bool = True,
        verbose: bool = True,
        reference_date: Optional[datetime] = None,
    ) -> List[InsiderTransaction]:
        """
        Scan for insider purchases.

        Args:
            days_back: Number of days to look back
            max_filings: Maximum filings to process
            use_daily_index: Use daily index (more data) vs Atom feed (faster)
            verbose: Print progress
            reference_date: Reference date for lookback (default: now)

        Returns:
            List of InsiderTransaction objects for purchases
        """
        if verbose:
            print("Fetching Form 4 filings...")

        filings = self.client.get_form4_filings(
            days_back=days_back,
            use_daily_index=use_daily_index,
            reference_date=reference_date,
        )

        if len(filings) > max_filings:
            if verbose:
                print(f"Found {len(filings)} filings, limiting to {max_filings}")
            filings = filings[:max_filings]
        elif verbose:
            print(f"Found {len(filings)} filings")

        purchases = []
        seen = set()
        processed = 0

        for filing in filings:
            xml_content = self.client.get_filing_xml(filing)
            if not xml_content:
                continue

            data = parse_form4_xml(xml_content)
            if not data:
                continue

            for tx in data["transactions"]:
                if not self._is_purchase(tx):
                    continue

                # Deduplicate
                tx_key = (
                    data["issuer"]["cik"],
                    data["owner"]["cik"],
                    tx["date"],
                    tx["shares"],
                    tx["price"],
                )
                if tx_key in seen:
                    continue
                seen.add(tx_key)

                purchases.append(
                    InsiderTransaction(
                        ticker=data["issuer"]["ticker"],
                        company_name=data["issuer"]["name"],
                        company_cik=data["issuer"]["cik"],
                        insider_name=data["owner"]["name"],
                        insider_cik=data["owner"]["cik"],
                        is_director=data["owner"]["is_director"],
                        is_officer=data["owner"]["is_officer"],
                        is_ten_percent_owner=data["owner"]["is_ten_percent_owner"],
                        officer_title=data["owner"]["officer_title"],
                        transaction_date=tx["date"],
                        transaction_code=tx["code"],
                        acquired_disposed=tx["acquired_disposed"],
                        shares=tx["shares"],
                        price_per_share=tx["price"],
                        total_value=tx["shares"] * tx["price"],
                        shares_owned_after=tx["shares_after"],
                        filing_date=filing.filed_at,
                        filing_url=filing.link,
                    )
                )

            processed += 1
            if verbose and processed % 50 == 0:
                print(f"  Processed {processed}/{len(filings)}, found {len(purchases)} purchases")

        return purchases

    def _is_purchase(self, tx: dict) -> bool:
        """Check if transaction is an open market purchase."""
        return (
            tx["code"] in ("P", "J")
            and tx["acquired_disposed"] == "A"
            and tx["shares"] > 0
            and tx["price"] > 0
        )
