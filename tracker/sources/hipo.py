"""Hipo.ro, a Romanian job board (keyword search).

Search is fuzzy (any word, descriptions included) and ranked by relevance, so it is
not ``complete``: we read the first pages of each query and stop once a page has no
compliance-looking titles. Closure comes from ``check_open`` ("This job is no longer
active!" on the job page). Card dates are refreshed daily, so they go in ``extra``
rather than ``posted``.
"""
from __future__ import annotations

import re
import time
from datetime import datetime

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .base import RawJob, html_to_text

BASE = "https://www.hipo.ro"
SEARCH = BASE + "/locuri-de-munca/cautajob/Toate-Domeniile/Toate-Orasele/{q}/{page}"
QUERIES = [
    "compliance",
    "conformitate",
    "AML",
    "KYC",
    "spalarea-banilor",
    "data-protection",
    "protectia-datelor",
    "GDPR",
    "DPO",
]
MAX_PAGES = 3
RELEVANT = re.compile(
    r"complian|conformitate|\baml\b|\bkyc\b|money laundering|sp[aă]l[aă]r|financial crime|sanction|"
    r"data protection|privacy|\bdpo\b|gdpr|protec[tț]i[ae] datelor",
    re.I,
)
# Recruiters that post other employers' roles as "Title - Employer".
REPOSTERS = {"Hipo Job Finder", "Top Jobs Romania"}
CLOSED_MARKERS = ("this job is no longer active", "acest job nu mai este activ")


def _text(node) -> str:
    return " ".join(node.text().split()) if node else ""


def parse_search(html: str) -> list[RawJob]:
    jobs = []
    for card in HTMLParser(html).css("div.job-item"):
        link = card.css_first("a.job-title")
        href = link.attributes.get("href") if link else None
        m = re.search(r"/locuri_de_munca/(\d+)/", href or "")
        if not m:
            continue
        title = " ".join((link.attributes.get("title") or _text(link)).split())
        company = _text(card.css_first(".company-name"))
        if company in REPOSTERS and " - " in title:
            title, company = (s.strip() for s in title.rsplit(" - ", 1))
        loc = next((_text(s) for s in card.css("span.text-truncate") if s.css_first("i.fa-map-marker-alt")), "")
        date = _text(card.css_first("small.text-truncate"))
        extra = {}
        try:
            extra["refreshed"] = datetime.strptime(date, "%d-%m-%Y").date().isoformat()
        except ValueError:
            pass
        jobs.append(
            RawJob(
                source="hipo",
                native_id=m.group(1),
                url=BASE + href,
                title=title,
                company=company,
                location=f"{loc}, Romania" if loc else "Romania",
                remote=loc.lower() == "remote",
                extra=extra,
            )
        )
    return jobs


def has_next_page(html: str) -> bool:
    return HTMLParser(html).css_first("div.pager a.page-next") is not None


def parse_posting(html: str) -> dict:
    tree = HTMLParser(html)
    for node in tree.css("#anunt-content style, #anunt-content script"):
        node.decompose()
    # Employers either paste free-form HTML (#anunt-content) or fill Hipo's form (div.content).
    free = tree.css_first("#anunt-content")
    if free:
        sections = [html_to_text(free.html)]
    else:
        sections = [html_to_text(c.html) for c in tree.css("div.content") if c.css_first("h2")]
    low = html.lower()
    return {
        "description": "\n\n".join(s for s in sections if s),
        "closed": any(m in low for m in CLOSED_MARKERS),
    }


class Hipo:
    name = "hipo"
    complete = False

    def __init__(self, delay: float = 1.0):
        self.delay = delay

    def fetch(self, client: httpx.Client) -> list[RawJob]:
        found: dict[str, RawJob] = {}
        for q in QUERIES:
            for page in range(1, MAX_PAGES + 1):
                r = client.get(SEARCH.format(q=q, page=page))
                r.raise_for_status()
                batch = parse_search(r.text)
                for job in batch:
                    found.setdefault(job.native_id, job)
                time.sleep(self.delay)
                if not has_next_page(r.text) or not any(RELEVANT.search(j.title) for j in batch):
                    break
        return list(found.values())

    def _posting(self, client: httpx.Client, url: str) -> dict | None:
        r = client.get(url)
        if r.status_code in (404, 410):
            return {"closed": True, "description": ""}
        if r.status_code != 200:
            return None
        time.sleep(self.delay)
        return parse_posting(r.text)

    def enrich(self, client: httpx.Client, job: RawJob) -> RawJob:
        info = self._posting(client, job.url)
        if info:
            job.description = info["description"]
        return job

    def check_open(self, client: httpx.Client, url: str) -> bool | None:
        info = self._posting(client, url)
        return None if info is None else not info["closed"]


SOURCE = Hipo()
