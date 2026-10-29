"""LinkedIn public (guest) job search, Romania.

Search is windowed by posting date, so it is not ``complete``: a job missing from
results only means it's older than the window. Closure comes from ``check_open``
on the job page ("No longer accepting applications").
"""
from __future__ import annotations

import re
import time

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .base import RawJob, html_to_text

SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
POSTING = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"
QUERIES = [
    "compliance",
    "conformitate",
    "AML",
    "KYC",
    "anti money laundering",
    "financial crime",
    "data protection",
    "privacy",
    "DPO",
]
PAGE_SIZE = 10
MAX_PAGES = 6
WEEK = "r604800"
MONTH = "r2592000"


def _text(node) -> str:
    return " ".join(node.text().split()) if node else ""


def parse_search(html: str) -> list[RawJob]:
    jobs = []
    for card in HTMLParser(html).css("div.base-search-card"):
        urn = card.attributes.get("data-entity-urn") or ""
        m = re.search(r"jobPosting:(\d+)", urn)
        link = card.css_first("a.base-card__full-link")
        if not m or not link:
            continue
        time_node = card.css_first("time")
        jobs.append(
            RawJob(
                source="linkedin",
                native_id=m.group(1),
                url=(link.attributes.get("href") or "").split("?")[0],
                title=_text(card.css_first(".base-search-card__title")),
                company=_text(card.css_first(".base-search-card__subtitle")),
                location=_text(card.css_first(".job-search-card__location")),
                posted=time_node.attributes.get("datetime") if time_node else None,
            )
        )
    return jobs


def parse_posting(html: str) -> dict:
    tree = HTMLParser(html)
    markup = tree.css_first(".show-more-less-html__markup")
    criteria = {
        _text(item.css_first(".description__job-criteria-subheader")): _text(
            item.css_first(".description__job-criteria-text")
        )
        for item in tree.css(".description__job-criteria-item")
    }
    return {
        "description": html_to_text(markup.html) if markup else "",
        "closed": "no longer accepting applications" in html.lower(),
        "seniority": criteria.get("Seniority level"),
        "employment_type": criteria.get("Employment type"),
    }


class LinkedIn:
    name = "linkedin"
    complete = False

    def __init__(self, window: str = WEEK, delay: float = 1.0):
        self.window = window
        self.delay = delay

    def _get(self, client: httpx.Client, url: str, **params) -> httpx.Response:
        for attempt in range(3):
            r = client.get(url, params=params)
            if r.status_code != 429:
                return r
            time.sleep(5 * (attempt + 1))
        return r

    def fetch(self, client: httpx.Client) -> list[RawJob]:
        found: dict[str, RawJob] = {}
        for q in QUERIES:
            for page in range(MAX_PAGES):
                r = self._get(
                    client, SEARCH, keywords=q, location="Romania", f_TPR=self.window, sortBy="DD",
                    start=page * PAGE_SIZE,
                )
                if r.status_code == 400 and page:  # past the last page
                    break
                r.raise_for_status()
                batch = parse_search(r.text)
                for job in batch:
                    found.setdefault(job.native_id, job)
                time.sleep(self.delay)
                if len(batch) < PAGE_SIZE:
                    break
        return list(found.values())

    def _posting(self, client: httpx.Client, job_id: str) -> dict | None:
        r = self._get(client, POSTING.format(id=job_id))
        if r.status_code in (404, 410):
            return {"closed": True, "description": ""}
        if r.status_code != 200:
            return None
        time.sleep(self.delay)
        return parse_posting(r.text)

    def enrich(self, client: httpx.Client, job: RawJob) -> RawJob:
        info = self._posting(client, job.native_id)
        if info:
            job.description = info["description"]
            job.extra.update({k: info[k] for k in ("seniority", "employment_type") if info.get(k)})
        return job

    def check_open(self, client: httpx.Client, url: str) -> bool | None:
        m = re.search(r"(\d{6,})/?$", url)
        if not m:
            return None
        info = self._posting(client, m.group(1))
        return None if info is None else not info["closed"]


SOURCE = LinkedIn()
