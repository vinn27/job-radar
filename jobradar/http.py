"""One shared requests.Session with timeout, retry and per-host throttling.

We are a guest hitting undocumented endpoints — stay polite: jittered delay
between requests to the same host, back off hard on 429/5xx.
"""

from __future__ import annotations

import random
import time
from urllib.parse import urlparse

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class SourceError(Exception):
    """A request failed after all retries."""


class PoliteSession:
    def __init__(self, min_delay: float = 2.0, max_delay: float = 4.0,
                 retries: int = 3, timeout: int = 15):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.retries = retries
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        })
        self._last_by_host: dict[str, float] = {}

    def _throttle(self, host: str) -> None:
        last = self._last_by_host.get(host)
        if last is not None:
            wait = random.uniform(self.min_delay, self.max_delay) - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)

    def fetch(self, url: str, params: dict | None = None) -> requests.Response:
        host = urlparse(url).netloc
        last_error: Exception | None = None
        for attempt in range(self.retries):
            self._throttle(host)
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                self._last_by_host[host] = time.monotonic()
                if resp.status_code == 200:
                    return resp
                retryable = resp.status_code == 429 or resp.status_code >= 500
                if retryable and attempt < self.retries - 1:
                    time.sleep(2 ** (attempt + 1) + random.uniform(0, 1))
                    continue
                raise SourceError(f"HTTP {resp.status_code} from {url}")
            except requests.RequestException as exc:
                last_error = exc
                self._last_by_host[host] = time.monotonic()
                if attempt < self.retries - 1:
                    time.sleep(2 ** (attempt + 1))
        raise SourceError(f"{url} failed after {self.retries} attempts: {last_error}")
