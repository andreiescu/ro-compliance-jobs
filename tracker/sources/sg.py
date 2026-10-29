"""Société Générale careers, every offer located in Romania (GSC / SG EBS Bucharest, BRD).

The search page no longer uses Algolia: it asks Drupal for a short-lived token and
posts "Quantum" (Sinequa) queries through ``/search-proxy.php``. We do the same with
a Romania location filter, which returns the full Romanian set in one or two pages,
so the source is ``complete``. The page also embeds every live offer id with its
country; we cross-check the API against it and raise when they disagree.
"""
from __future__ import annotations

import json
import re

import httpx

from .base import RawJob, generic_check_open, html_to_text

BASE = "https://careers.societegenerale.com"
SEARCH_PAGE = BASE + "/en/search"
TOKEN = BASE + "/sg-careers-offers/get-token"
PROXY = BASE + "/search-proxy.php"
PAGE_SIZE = 100
MAX_PAGES = 5


def parse_settings(html: str) -> dict:
    """csrf token, API base url and the ids of live Romanian offers from the search page."""
    m = re.search(r'data-drupal-selector="drupal-settings-json">(.*?)</script>', html, re.S)
    if not m:
        raise ValueError("SG: drupal settings not found on search page")
    settings = json.loads(m.group(1))
    quantum = settings["quantum"]
    return {
        "csrf": settings.get("csrfToken", ""),
        "base_url": quantum["base_url"],
        "romania_ids": {k for k, v in quantum.get("all_offers", {}).items() if v.get("country") == "ROU"},
    }


def query_body(skip_from: int) -> dict:
    return {
        "profile": "ces_profile_sgcareers",
        "query": {
            "advanced": [
                {"type": "simple", "name": "sourcestr6", "op": "eq", "value": "job"},
                {"type": "multi", "name": "sourcecsv1", "op": "eq", "values": ["ROU"]},
            ],
            "skipCount": PAGE_SIZE,
            "skipFrom": skip_from,
            "sort": "sourcedatetime1.desc",
        },
        "lang": "en",
        "responseType": "SearchResult",
    }


def company(unit: str) -> str:
    """BRD offers are BRD's; SG EBS / GSC read as "Société Générale European Business Services"."""
    if unit.upper().startswith("BRD"):
        return "BRD - Groupe Société Générale"
    unit = re.sub(r"^SG\s+", "", unit)
    return f"Société Générale {unit}".strip()


def parse_results(data: dict) -> list[RawJob]:
    jobs: dict[str, RawJob] = {}
    for doc in data["Result"]["Docs"]:
        ref, url = doc.get("sourcestr4"), doc.get("url1") or doc.get("resulturl") or ""
        if not ref or not url:
            continue
        if ref in jobs and "/en/" not in url:  # same offer indexed in French too
            continue
        location = doc.get("sourcestr7") or "Romania"
        if "romania" not in location.lower():
            location += ", Romania"
        posted = (doc.get("sourcedatetime1") or "")[:10] or None
        jobs[ref] = RawJob(
            source="sg",
            native_id=ref,
            url=url,
            title=" ".join((doc.get("title") or "").split()),
            company=company(doc.get("sourcestr15") or ""),
            location=location,
            posted=posted,
            description=html_to_text(doc.get("sourcevarchar2") or ""),
            remote=False,
            extra={k: v for k, v in (("contract", doc.get("sourcestr8")), ("family", doc.get("sourcestr10")),
                                     ("expires", (doc.get("sourcedatetime2") or "")[:10])) if v},
        )
    return list(jobs.values())


class SocieteGenerale:
    name = "sg"
    complete = True

    def fetch(self, client: httpx.Client) -> list[RawJob]:
        r = client.get(SEARCH_PAGE)
        r.raise_for_status()
        settings = parse_settings(r.text)
        t = client.get(TOKEN, headers={"X-Requested-With": "XMLHttpRequest", "X-CSRF-Token": settings["csrf"]})
        t.raise_for_status()
        headers = {
            "X-Proxy-URL": settings["base_url"] + "/search-profile",
            "Authorization-API": "Bearer " + t.json()["token"],
        }
        found: dict[str, RawJob] = {}
        total = None
        for page in range(MAX_PAGES):
            r = client.post(PROXY, json=query_body(page * PAGE_SIZE), headers=headers)
            r.raise_for_status()
            data = r.json()
            total = data["TotalCount"]
            for job in parse_results(data):
                found.setdefault(job.native_id, job)
            if (page + 1) * PAGE_SIZE >= total:
                break
        else:
            raise RuntimeError(f"SG: more than {MAX_PAGES * PAGE_SIZE} Romanian offers, raise MAX_PAGES")
        expected = settings["romania_ids"]
        missing = expected - found.keys()
        if expected and len(missing) > max(3, len(expected) // 10):
            raise RuntimeError(f"SG: API returned {len(found)} offers but the page lists {len(expected)}")
        return list(found.values())

    def check_open(self, client: httpx.Client, url: str) -> bool | None:
        return generic_check_open(client, url)


SOURCE = SocieteGenerale()
