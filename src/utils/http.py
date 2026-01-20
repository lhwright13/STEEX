"""HTTP client with SEC-compliant rate limiting."""

import time
from typing import Optional

import requests

# SEC requires user agent with contact info
DEFAULT_USER_AGENT = "STEEX Research contact@example.com"
SEC_BASE_URL = "https://www.sec.gov"
SEC_DATA_URL = "https://data.sec.gov"

# Rate limiting: SEC allows 10 requests/second
REQUEST_DELAY = 0.15  # 150ms between requests


class SecClient:
    """HTTP client for SEC EDGAR with rate limiting."""

    def __init__(self, user_agent: str = DEFAULT_USER_AGENT, delay: float = REQUEST_DELAY):
        self.headers = {"User-Agent": user_agent}
        self.delay = delay
        self._last_request = 0.0

    def _rate_limit(self):
        """Enforce rate limiting between requests."""
        elapsed = time.time() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.time()

    def get(self, url: str, timeout: int = 30) -> Optional[requests.Response]:
        """Make a GET request with rate limiting."""
        self._rate_limit()
        try:
            response = requests.get(url, headers=self.headers, timeout=timeout)
            return response
        except requests.RequestException:
            return None

    def get_text(self, url: str, timeout: int = 30) -> Optional[str]:
        """Get text content from URL."""
        response = self.get(url, timeout)
        if response and response.status_code == 200:
            return response.text
        return None

    def get_bytes(self, url: str, timeout: int = 30) -> Optional[bytes]:
        """Get binary content from URL."""
        response = self.get(url, timeout)
        if response and response.status_code == 200:
            return response.content
        return None
