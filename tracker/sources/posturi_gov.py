"""posturi.gov.ro, Romanian public-sector vacancies (WordPress REST, ``pg_job`` posts).

Full-text search is useless here ("în conformitate cu" and GDPR boilerplate are in
nearly every announcement), so we search titles only (``search_columns=post_title``,
accent-insensitive). The API only holds announcements whose deadline hasn't passed,
so the result is the full current set and the source is ``complete``. The employer
is only on the job page, which we fetch for titles the classifier keeps.
"""
from __future__ import annotations

import html
import json
import re
import time
from datetime import date

import httpx

from ..classify import category
from .base import RawJob, html_to_text

API = "https://posturi.gov.ro/wp-json/wp/v2/pg_job"
TITLE_TERMS = [
    "conformitate",
    "complian",
    "datelor",
    "date personale",
    "caracter personal",
    "GDPR",
    "DPO",
    "spalare",
    "integritate",
    "control intern",
    "antifrauda",
]
PER_PAGE = 100
MAX_PAGES = 3
MAX_PAGE_FETCHES = 20
MIN_OPEN_JOBS = 300
FIELDS = "id,date,link,title,content,meta,_links,_embedded"


def _terms(post: dict, taxonomy: str) -> list[str]:
    groups = post.get("_embedded", {}).get("wp:term", [])
    return [html.unescape(t["name"]) for g in groups for t in g if t.get("taxonomy") == taxonomy]


def parse_jobs(data: list[dict], today: date | None = None) -> list[RawJob]:
    today = today or date.today()
    jobs = []
    for post in data:
        meta = post.get("meta") or {}
        deadline = meta.get("pg_deadline_date") or None
        if deadline and deadline < today.isoformat():
            continue
        place = _terms(post, "pg_job_oras") + _terms(post, "pg_job_judet")
        extra = {"deadline": deadline} if deadline else {}
        kind = _terms(post, "pg_job_categorie")
        if kind:
            extra["kind"] = kind[0]
        jobs.append(
            RawJob(
                source="posturi_gov",
                native_id=str(post["id"]),
                url=post["link"],
                title=" ".join(html.unescape(post["title"]["rendered"]).split()),
                company="",
                location=", ".join(dict.fromkeys(place + ["Romania"])),
                posted=meta.get("pg_publish_date") or post["date"][:10],
                description=html_to_text(post.get("content", {}).get("rendered", "")),
                extra=extra,
            )
        )
    return jobs


def parse_page(page: str) -> dict:
    """Employer and deadline from a job page's JobPosting JSON-LD."""
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        if data.get("@type") == "JobPosting":
            return {
                "company": (data.get("hiringOrganization") or {}).get("name", ""),
                "valid_through": (data.get("validThrough") or "")[:10] or None,
            }
    return {"company": "", "valid_through": None}


class PosturiGov:
    name = "posturi_gov"
    complete = True

    def __init__(self, delay: float = 1.0):
        self.delay = delay

    def fetch(self, client: httpx.Client) -> list[RawJob]:
        # Matches are rare, so an empty answer must come from a healthy API.
        r = client.get(API, params={"per_page": 1, "_fields": "id"})
        r.raise_for_status()
        if int(r.headers.get("X-WP-Total", "0")) < MIN_OPEN_JOBS:
            raise RuntimeError(f"posturi_gov: API lists only {r.headers.get('X-WP-Total')} open jobs")
        found: dict[str, RawJob] = {}
        for term in TITLE_TERMS:
            for page in range(1, MAX_PAGES + 1):
                r = client.get(API, params={"search": term, "search_columns": "post_title", "per_page": PER_PAGE,
                                            "page": page, "_embed": "wp:term", "_fields": FIELDS})
                r.raise_for_status()
                for job in parse_jobs(r.json()):
                    found.setdefault(job.native_id, job)
                time.sleep(self.delay)
                pages = int(r.headers.get("X-WP-TotalPages", "0"))
                if page >= pages:
                    break
            else:
                raise RuntimeError(f"posturi_gov: more than {MAX_PAGES} pages for {term!r}")
        jobs = [j for j in found.values() if category(j.title)]
        if len(jobs) > MAX_PAGE_FETCHES:
            raise RuntimeError(f"posturi_gov: {len(jobs)} matching titles, raise MAX_PAGE_FETCHES")
        for job in jobs:
            r = client.get(job.url)
            r.raise_for_status()
            job.company = parse_page(r.text)["company"] or "posturi.gov.ro"
            time.sleep(self.delay)
        return jobs

    def check_open(self, client: httpx.Client, url: str) -> bool | None:
        try:
            r = client.get(url)
        except httpx.HTTPError:
            return None
        if r.status_code in (404, 410):
            return False
        if r.status_code != 200:
            return None
        valid = parse_page(r.text)["valid_through"]
        return not (valid and valid < date.today().isoformat())


SOURCE = PosturiGov()
