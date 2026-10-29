"""Shared types and HTTP helpers for job sources.

A source module exposes a ``SOURCE`` object implementing the ``Source`` protocol:

- ``fetch(client)`` returns every relevant-looking listing it can see right now.
  It may over-return; the classifier drops titles/locations that are out of scope.
- ``complete``: True when ``fetch`` returns the source's *full* current list for
  our queries, so a listing missing from it is evidence the job closed. False for
  windowed searches (e.g. LinkedIn's "last 7 days"), where absence means nothing.
- ``check_open(client, url)`` (optional): fetch one listing and say whether it is
  still open (True), closed (False) or unknown (None, e.g. blocked).
- ``enrich(client, job)`` (optional): return the job with ``description`` filled.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Protocol

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

# Search terms used by keyword-driven sources (English + Romanian).
KEYWORDS = [
    "compliance",
    "conformitate",
    "AML",
    "KYC",
    "anti money laundering",
    "financial crime",
    "data protection",
    "DPO",
]


@dataclass
class RawJob:
    source: str
    native_id: str
    url: str
    title: str
    company: str
    location: str
    posted: str | None = None  # ISO date (YYYY-MM-DD) when known
    description: str = ""  # plain text
    remote: bool = False
    extra: dict = field(default_factory=dict)


class Source(Protocol):
    name: str
    complete: bool

    def fetch(self, client: httpx.Client) -> list[RawJob]: ...


class RetryingTransport(httpx.HTTPTransport):
    """Retries timeouts and dropped connections, so one slow response doesn't fail a whole source."""

    def __init__(self, attempts: int = 3, backoff: float = 3.0):
        super().__init__(retries=1)
        self.attempts, self.backoff = attempts, backoff

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        for attempt in range(self.attempts):
            try:
                return super().handle_request(request)
            except httpx.TransportError:
                if attempt == self.attempts - 1:
                    raise
                time.sleep(self.backoff * (attempt + 1))
        raise AssertionError("unreachable")


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9,ro;q=0.8"},
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
        transport=RetryingTransport(),
    )


def html_to_text(html: str) -> str:
    """Flatten an HTML fragment to readable plain text, keeping paragraph breaks."""
    if not html:
        return ""
    html = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", html)
    html = re.sub(r"(?i)<li[^>]*>", "• ", html)
    text = HTMLParser(f"<div>{html}</div>").text(separator="")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


CLOSED_PHRASES = [
    "no longer accepting applications",
    "this job is no longer available",
    "job is no longer available",
    "position has been filled",
    "this position is no longer open",
    "no longer open for recruitment",
    "job has expired",
    "anunțul a expirat",
    "anuntul a expirat",
    "nu mai este disponibil",
]


def generic_check_open(client: httpx.Client, url: str) -> bool | None:
    """Best-effort open/closed check for any listing URL."""
    try:
        r = client.get(url)
    except httpx.HTTPError:
        return None
    if r.status_code in (404, 410):
        return False
    if r.status_code != 200:
        return None
    low = r.text.lower()
    if any(p in low for p in CLOSED_PHRASES):
        return False
    return True
