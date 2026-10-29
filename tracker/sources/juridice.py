"""cariere.juridice.ro, Romania's legal careers board (WordPress REST API).

Posts are searched by keyword over the last ``WINDOW_DAYS`` only, so the source is
not ``complete``. Posts are never taken down when a role is filled, so
``check_open`` treats a post as closed once it's 404 or older than the window.
"""
from __future__ import annotations

import html
import re
import time
from datetime import date, timedelta

import httpx

from .base import RawJob, generic_check_open, html_to_text

API = "https://cariere.juridice.ro/wp-json/wp/v2/posts"
QUERIES = ["compliance", "conformitate", "AML", "KYC", "spălarea banilor", "data protection", "GDPR", "DPO"]
DATA_PROTECTION_CATEGORY = 183
WINDOW_DAYS = 75
PER_PAGE = 100
MAX_PAGES = 2
FIELDS = "id,date,link,title,content"
HIRING = re.compile(
    r"\s+(?:is looking for|is recruiting|is hiring|seeks|caută|cauta|recrutează|recruteaza|angajează|angajeaza)\s+"
    r"(?:(?:an?|un|o)\s+)?",
    re.I,
)
CITIES = re.compile(
    r"Bucure[șşs]ti|Bucharest|Cluj[- ]Napoca|Ia[șşs]i|Timi[șşs]oara|Bra[șşs]ov|Sibiu|Constan[țţt]a|Craiova|Oradea|"
    r"Ploie[șşs]ti|Pite[șşs]ti|Arad|Gala[țţt]i|Bac[ăa]u|Suceava|T[âa]rgu[- ]Mure[șşs]",
    re.I,
)
REMOTE = re.compile(r"\b(full[- ]?remote|100% remote|fully remote)\b", re.I)


def split_title(post_title: str) -> tuple[str, str]:
    """'NETOPIA PAYMENTS is looking for a Legal & Compliance Officer' -> (role, company)."""
    title = " ".join(html.unescape(post_title).split())
    parts = HIRING.split(title, maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[1].strip(), parts[0].strip()
    return title, ""


def parse_posts(data: list[dict]) -> list[RawJob]:
    jobs = []
    for post in data:
        title, company = split_title(post["title"]["rendered"])
        text = html_to_text(post.get("content", {}).get("rendered", ""))
        city = CITIES.search(text)
        jobs.append(
            RawJob(
                source="juridice",
                native_id=str(post["id"]),
                url=post["link"],
                title=title,
                company=company or "cariere.juridice.ro",
                location=f"{city.group(0)}, Romania" if city else "Romania",
                posted=post["date"][:10],
                description=text,
                remote=bool(REMOTE.search(text)),
            )
        )
    return jobs


def post_month(url: str) -> date | None:
    m = re.search(r"juridice\.ro/(\d{4})/(\d{2})/", url)
    return date(int(m.group(1)), int(m.group(2)), 1) if m else None


class Juridice:
    name = "juridice"
    complete = False

    def __init__(self, delay: float = 1.0):
        self.delay = delay

    def fetch(self, client: httpx.Client) -> list[RawJob]:
        after = (date.today() - timedelta(days=WINDOW_DAYS)).isoformat() + "T00:00:00"
        searches = [{"search": q} for q in QUERIES] + [{"categories": DATA_PROTECTION_CATEGORY}]
        found: dict[str, RawJob] = {}
        for params in searches:
            for page in range(1, MAX_PAGES + 1):
                r = client.get(API, params={**params, "after": after, "per_page": PER_PAGE, "page": page,
                                            "_fields": FIELDS})
                r.raise_for_status()
                for job in parse_posts(r.json()):
                    found.setdefault(job.native_id, job)
                time.sleep(self.delay)
                if page >= int(r.headers.get("X-WP-TotalPages", "1")):
                    break
        return list(found.values())

    def check_open(self, client: httpx.Client, url: str) -> bool | None:
        month = post_month(url)
        if month and month < date.today() - timedelta(days=WINDOW_DAYS + 31):
            return False
        return generic_check_open(client, url)


SOURCE = Juridice()
