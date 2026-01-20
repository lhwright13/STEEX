# STEEX
Safe Trading Environment for Experimental Xyz
SEC trading analysis tools. Scans insider trading filings (Form 4) to detect buying signals.

## Quick Start

```bash
# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install package in development mode
pip install -e .

# Run insider scanner
python scripts/scan_insiders.py
```

## Features

- **Insider Buy Scanner**: Detects open market purchases from Form 4 filings
- **Cluster Buy Detection**: Finds stocks where multiple insiders are buying (strongest signal)
- **Signal Scoring**: Ranks cluster buys by number of insiders, purchase value, and roles
- **Free API**: Uses official SEC EDGAR API (no API key needed)

## Usage

```bash
# Default scan (3 days, daily index)
python scripts/scan_insiders.py

# Quick scan (Atom feed, faster but less data)
python scripts/scan_insiders.py --fast

# Custom lookback period
python scripts/scan_insiders.py --days 7

# Process more filings
python scripts/scan_insiders.py --max 300
```

## Project Structure

```
steex/
  src/
    sec/
      client.py          # SEC EDGAR API client
      models.py          # Data models
      parsers/           # XML/index file parsers
      scanners/          # Analysis scanners
    utils/
      http.py            # HTTP client with rate limiting
  scripts/
    scan_insiders.py     # CLI entry point
  tests/
```

## Signal Interpretation

**Transaction Codes:**
- `P` = Open market purchase (bullish)
- `J` = Other/late-filed (research shows 20% outperformance)
- `S` = Sale
- `A` = Award/grant

**Cluster Buy Scoring (0-100):**
- +20 per unique insider buying
- +10/20/30 for value >$100k/$500k/$1M
- +10 per officer, +5 per director
- -3 per 10% owner (less meaningful)
