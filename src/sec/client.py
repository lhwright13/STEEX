"""SEC EDGAR API client."""

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional

from utils.http import SecClient, SEC_BASE_URL
from .models import Filing
from .parsers.daily_index import parse_daily_index


class EdgarClient:
    """Client for SEC EDGAR API."""

    def __init__(self, user_agent: str = None):
        self.http = SecClient(user_agent) if user_agent else SecClient()

    def get_form4_filings(
        self,
        days_back: int = 7,
        use_daily_index: bool = True,
        reference_date: Optional[datetime] = None,
    ) -> list[Filing]:
        """
        Fetch recent Form 4 filings.

        Args:
            days_back: Number of days to look back
            use_daily_index: Use daily index (more data) or Atom feed (faster)
            reference_date: Reference date for lookback (default: now)

        Returns:
            List of Filing objects
        """
        if use_daily_index:
            filings = self._get_filings_from_daily_index(days_back, reference_date)
            if filings:
                return filings

        return self._get_filings_from_atom()

    def _get_filings_from_daily_index(
        self, days_back: int, reference_date: Optional[datetime] = None
    ) -> list[Filing]:
        """Fetch filings from daily index files."""
        all_filings = []
        today = reference_date or datetime.now()

        for i in range(days_back):
            date = today - timedelta(days=i)
            if date.weekday() >= 5:  # Skip weekends
                continue

            date_str = date.strftime("%Y%m%d")
            year = date.strftime("%Y")
            qtr = f"QTR{(date.month - 1) // 3 + 1}"

            idx_url = f"{SEC_BASE_URL}/Archives/edgar/daily-index/{year}/{qtr}/company.{date_str}.idx"
            content = self.http.get_text(idx_url)

            if content:
                filings = parse_daily_index(content, form_type="4")
                all_filings.extend(filings)

        return all_filings

    def _get_filings_from_atom(self, count: int = 100) -> list[Filing]:
        """Fetch filings from Atom feed."""
        url = f"{SEC_BASE_URL}/cgi-bin/browse-edgar?action=getcurrent&type=4&count={count}&output=atom"
        content = self.http.get_bytes(url)

        if not content:
            return []

        filings = []
        seen = set()

        try:
            root = ET.fromstring(content)
            ns = {"atom": "http://www.w3.org/2005/Atom"}

            for entry in root.findall("atom:entry", ns):
                link_elem = entry.find("atom:link", ns)
                updated = entry.findtext("atom:updated", "", ns)
                entry_id = entry.findtext("atom:id", "", ns)

                if link_elem is None:
                    continue

                link = link_elem.get("href", "")
                if link.startswith("/"):
                    link = f"{SEC_BASE_URL}{link}"

                acc_match = re.search(r"accession-number=(\d+-\d+-\d+)", entry_id)
                if not acc_match:
                    continue

                accession = acc_match.group(1)
                if accession in seen:
                    continue
                seen.add(accession)

                cik_match = re.search(r"/data/(\d+)/", link)
                cik = cik_match.group(1) if cik_match else ""

                filings.append(
                    Filing(accession=accession, cik=cik, link=link, filed_at=updated)
                )

        except ET.ParseError:
            pass

        return filings

    def get_filing_xml(self, filing: Filing) -> Optional[bytes]:
        """
        Fetch the XML content for a filing.

        Args:
            filing: Filing object with link to index page

        Returns:
            Raw XML bytes or None
        """
        # Get index page
        index_html = self.http.get_text(filing.link)
        if not index_html:
            return None

        # Find XML files
        xml_paths = re.findall(r'href="([^"]+\.xml)"', index_html)

        # Prefer raw XML over xsl-rendered
        for xml_path in xml_paths:
            if "xsl" in xml_path.lower():
                continue

            if xml_path.startswith("http"):
                xml_url = xml_path
            elif xml_path.startswith("/"):
                xml_url = f"{SEC_BASE_URL}{xml_path}"
            else:
                base = filing.link.rsplit("/", 1)[0]
                xml_url = f"{base}/{xml_path}"

            content = self.http.get_bytes(xml_url)
            if content:
                return content

        return None
