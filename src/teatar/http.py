"""Shared HTTP client: browser-like UA, retries with backoff, and a polite per-host rate limit."""

from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit

import httpx

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
RETRY_STATUS = {429, 500, 502, 503, 504}


class Http:
    def __init__(self, min_interval: float = 0.5, retries: int = 3, timeout: float = 30.0):
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Language": "sr,en;q=0.8"},
            timeout=timeout,
            follow_redirects=True,
        )
        self.min_interval = min_interval
        self.retries = retries
        self._last: dict[str, float] = {}

    def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        wait = self._last.get(host, 0) + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        for attempt in range(self.retries + 1):
            self._throttle(url)
            try:
                resp = self.client.request(method, url, **kwargs)
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    raise
                log.warning("%s %s failed (%s), retrying", method, url, exc)
            else:
                if resp.status_code not in RETRY_STATUS or attempt == self.retries:
                    resp.raise_for_status()
                    return resp
                log.warning("%s %s -> HTTP %s, retrying", method, url, resp.status_code)
            time.sleep(2**attempt)
        raise RuntimeError("unreachable")

    def get(self, url: str, **kwargs) -> httpx.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs) -> httpx.Response:
        return self.request("POST", url, **kwargs)

    def close(self) -> None:
        self.client.close()
