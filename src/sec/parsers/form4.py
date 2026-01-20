"""Parser for SEC Form 4 XML filings."""

import xml.etree.ElementTree as ET
from typing import Optional


def parse_form4_xml(xml_content: bytes) -> Optional[dict]:
    """
    Parse Form 4 XML content to extract transaction details.

    Args:
        xml_content: Raw XML bytes

    Returns:
        Dict with issuer, owner, and transactions data, or None if parsing fails
    """
    if b"<ownershipDocument>" not in xml_content:
        return None

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None

    # Extract issuer info
    issuer = root.find("issuer")
    if issuer is None:
        return None

    issuer_data = {
        "cik": (issuer.findtext("issuerCik") or "").lstrip("0"),
        "name": issuer.findtext("issuerName") or "",
        "ticker": issuer.findtext("issuerTradingSymbol") or "",
    }

    # Extract reporting owner info
    owner = root.find("reportingOwner")
    if owner is None:
        return None

    owner_id = owner.find("reportingOwnerId")
    owner_rel = owner.find("reportingOwnerRelationship")

    owner_data = {
        "cik": "",
        "name": "",
        "is_director": False,
        "is_officer": False,
        "is_ten_percent_owner": False,
        "officer_title": "",
    }

    if owner_id is not None:
        owner_data["cik"] = (owner_id.findtext("rptOwnerCik") or "").lstrip("0")
        owner_data["name"] = owner_id.findtext("rptOwnerName") or ""

    if owner_rel is not None:
        owner_data["is_director"] = (
            owner_rel.findtext("isDirector") or ""
        ).lower() == "true"
        owner_data["is_officer"] = (
            owner_rel.findtext("isOfficer") or ""
        ).lower() == "true"
        owner_data["is_ten_percent_owner"] = (
            owner_rel.findtext("isTenPercentOwner") or ""
        ).lower() == "true"
        owner_data["officer_title"] = owner_rel.findtext("officerTitle") or ""

    # Extract transactions
    transactions = []
    non_deriv_table = root.find("nonDerivativeTable")

    if non_deriv_table is not None:
        for trans in non_deriv_table.findall("nonDerivativeTransaction"):
            tx = _parse_transaction(trans)
            if tx:
                transactions.append(tx)

    return {
        "issuer": issuer_data,
        "owner": owner_data,
        "transactions": transactions,
    }


def _parse_transaction(trans_elem) -> Optional[dict]:
    """Parse a single transaction element."""
    trans_coding = trans_elem.find("transactionCoding")
    trans_amounts = trans_elem.find("transactionAmounts")
    post_trans = trans_elem.find("postTransactionAmounts")

    if trans_coding is None or trans_amounts is None:
        return None

    trans_code = trans_coding.findtext("transactionCode") or ""

    # Get shares
    shares = _get_float_value(trans_amounts, "transactionShares")

    # Get price
    price_elem = trans_amounts.find("transactionPricePerShare")
    price = 0.0
    if price_elem is not None:
        price_val = (price_elem.findtext("value") or "0").strip()
        try:
            price = float(price_val) if price_val else 0.0
        except ValueError:
            price = 0.0

    # Get acquired/disposed code
    ad_elem = trans_amounts.find("transactionAcquiredDisposedCode")
    ad_code = ad_elem.findtext("value") or "" if ad_elem is not None else ""

    # Get shares owned after
    shares_after = 0.0
    if post_trans is not None:
        shares_after = _get_float_value(post_trans, "sharesOwnedFollowingTransaction")

    # Get transaction date
    trans_date_elem = trans_elem.find("transactionDate")
    trans_date = trans_date_elem.findtext("value") or "" if trans_date_elem else ""

    return {
        "date": trans_date,
        "code": trans_code,
        "acquired_disposed": ad_code,
        "shares": shares,
        "price": price,
        "shares_after": shares_after,
    }


def _get_float_value(parent_elem, child_name: str) -> float:
    """Extract float value from nested element."""
    elem = parent_elem.find(child_name)
    if elem is None:
        return 0.0
    try:
        return float(elem.findtext("value") or "0")
    except ValueError:
        return 0.0
