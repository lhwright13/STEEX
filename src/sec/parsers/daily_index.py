"""Parser for SEC daily index files."""

import re
from typing import Optional

from ..models import Filing

SEC_BASE_URL = "https://www.sec.gov"


def parse_daily_index(index_content: str, form_type: str = "4") -> list[Filing]:
    """
    Parse SEC daily index file content.

    Args:
        index_content: Raw text content of the index file
        form_type: Form type to filter for (default: "4" for Form 4)

    Returns:
        List of Filing objects
    """
    filings = []
    seen_accessions = set()

    for line in index_content.split("\n"):
        if not line.strip():
            continue
        if line.startswith("Description") or line.startswith("-"):
            continue
        if "Company Name" in line or "Form Type" in line:
            continue

        filing = _parse_index_line(line, form_type)
        if filing and filing.accession not in seen_accessions:
            seen_accessions.add(filing.accession)
            filings.append(filing)

    return filings


def _parse_index_line(line: str, form_type: str) -> Optional[Filing]:
    """Parse a single line from the index file."""
    parts = line.split()
    if len(parts) < 4:
        return None

    # Find the form type in the line
    form_idx = -1
    for idx, part in enumerate(parts):
        if part == form_type:
            form_idx = idx
            break

    if form_idx == -1:
        return None

    try:
        cik = parts[form_idx + 1]
        filed_date = parts[form_idx + 2]
        file_path = parts[-1]

        if "edgar/data" not in file_path:
            return None

        # Extract accession number from file path
        acc_match = re.search(r"(\d{10}-\d{2}-\d{6})", file_path)
        if not acc_match:
            return None

        accession = acc_match.group(1)
        link = f"{SEC_BASE_URL}/Archives/{file_path.replace('.txt', '-index.htm')}"

        return Filing(
            accession=accession,
            cik=cik,
            link=link,
            filed_at=filed_date,
            file_path=file_path,
        )

    except (IndexError, ValueError):
        return None
