"""Workday career sites (public CXS JSON API), Romania only.

Each tenant is narrowed to Romania with its own country (or location) facet and
then listed in full, with no keyword filter: the Romania sets are small (tens to
~200 postings) and a full list is what makes this source ``complete``. The
classifier drops the off-topic titles. Any tenant failure raises.

Postings shown as "N Locations" (or with no location) in the list are resolved through the detail call
(only for titles the classifier would keep) so the location names the Romanian
city. ``posted`` comes from the detail call's absolute ``startDate`` in
``enrich``. The list's "Posted N Days Ago" is relative to the tenant's timezone
and would shift by a day during part of each day, so it only goes into ``extra``.
"""
from __future__ import annotations

import re
import time
from datetime import date, timedelta
from urllib.parse import urlsplit

import httpx

from ..classify import category
from .base import RawJob, html_to_text

# (host, site, company). Romania job counts from 2026-10-29 are in the comments.
TENANTS: list[tuple[str, str, str]] = [
    ("accenture.wd103.myworkdayjobs.com", "AccentureCareers", "Accenture"),  # 107
    ("ing.wd3.myworkdayjobs.com", "ICSGBLCOR", "ING Hubs Romania"),  # 68
    ("genpact.wd108.myworkdayjobs.com", "External_Careers", "Genpact"),  # 167
    ("pwc.wd3.myworkdayjobs.com", "Global_Experienced_Careers", "PwC"),  # 68
    ("db.wd3.myworkdayjobs.com", "DBWebsite", "Deutsche Bank"),  # 101
    ("nngroup.wd3.myworkdayjobs.com", "WDExternal", "NN"),  # 5
    ("lseg.wd3.myworkdayjobs.com", "Careers", "LSEG"),  # 51
    ("ayvens.wd3.myworkdayjobs.com", "AyvensCareers", "Ayvens"),  # 20
    ("visa.wd5.myworkdayjobs.com", "Visa", "Visa"),  # 3
    ("broadridge.wd5.myworkdayjobs.com", "Careers", "Broadridge"),  # 14
    ("ssctech.wd1.myworkdayjobs.com", "SSCTechnologies", "SS&C Technologies"),  # 3
    ("morningstar.wd5.myworkdayjobs.com", "morningstar", "Morningstar"),  # 1
    ("wk.wd3.myworkdayjobs.com", "External", "Wolters Kluwer"),  # 11
    ("adobe.wd5.myworkdayjobs.com", "external_experienced", "Adobe"),  # 17
    ("pg.wd5.myworkdayjobs.com", "1000", "Procter & Gamble"),  # 20
    ("philips.wd3.myworkdayjobs.com", "jobs-and-careers", "Philips"),  # 3
    ("iqvia.wd1.myworkdayjobs.com", "IQVIA", "IQVIA"),  # 41
    ("thermofisher.wd5.myworkdayjobs.com", "ThermoFisherCareers", "Thermo Fisher Scientific"),  # 30
    ("medtronic.wd1.myworkdayjobs.com", "MedtronicCareers", "Medtronic"),  # 10
    ("novartis.wd3.myworkdayjobs.com", "Novartis_Careers", "Novartis"),  # 11
    ("sanofi.wd3.myworkdayjobs.com", "SanofiCareers", "Sanofi"),  # 1
    ("zentiva.wd3.myworkdayjobs.com", "Zentiva", "Zentiva"),  # 22
    ("icon.wd3.myworkdayjobs.com", "broadbean_external", "ICON plc"),  # 53
    ("parexel.wd1.myworkdayjobs.com", "Parexel_External_Careers", "Parexel"),  # 24
    ("syneoshealth.wd12.myworkdayjobs.com", "Syneos_Health_External_Site", "Syneos Health"),  # 19
    ("fortrea.wd1.myworkdayjobs.com", "Fortrea", "Fortrea"),  # 20
]
PAGE_SIZE = 20  # the CXS API rejects larger pages
ROMANIA_ID = "f2e609fe92974a55a05fc1cdc2852122"  # Workday's global id for the Romania country
RO_PLACE = re.compile(
    r"roman|\brou\b|bucharest|bucure|cluj|ia[sș]i\b|timi[sș]oara|bra[sș]ov|sibiu|craiova|oradea|constan[tț]a|"
    r"ploie[sș]ti|pite[sș]ti|t[aâ]rgu.?mure",
    re.I,
)
MULTI = re.compile(r"^\d+ Locations?$", re.I)


def _flat_facets(facets: list[dict]) -> list[tuple[str, str, str, int]]:
    out = []
    for f in facets:
        for v in f.get("values", []):
            if "facetParameter" in v:
                out += _flat_facets([v])
            else:
                out.append((f["facetParameter"], v.get("id", ""), v.get("descriptor", ""), v.get("count") or 0))
    return out


def romania_facets(facets: list[dict]) -> dict[str, list[str]]:
    """``appliedFacets`` restricting a tenant to Romania, or {} when it has no Romanian postings."""
    values = _flat_facets(facets)
    for param, vid, desc, _ in values:
        if vid == ROMANIA_ID or desc.strip().lower() == "romania":
            return {param: [vid]}
    # No country facet: take every Romanian location from the single facet that lists the most.
    by_param: dict[str, list[tuple[str, int]]] = {}
    for param, vid, desc, count in values:
        if RO_PLACE.search(desc):
            by_param.setdefault(param, []).append((vid, count))
    if not by_param:
        return {}
    param = max(by_param, key=lambda p: sum(c for _, c in by_param[p]))
    return {param: [vid for vid, _ in by_param[param]]}


def parse_posted(text: str | None, today: date) -> str | None:
    """"Posted Today / Yesterday / N Days Ago" → ISO date; None for "30+ Days Ago" or unknown."""
    t = (text or "").lower()
    if "30+" in t:
        return None
    if "today" in t:
        days = 0
    elif "yesterday" in t:
        days = 1
    else:
        m = re.search(r"(\d+)\s+days?\s+ago", t)
        if not m:
            return None
        days = int(m.group(1))
    return (today - timedelta(days=days)).isoformat()


def _romanian(loc: str) -> str:
    return loc if re.search(r"roman", loc, re.I) else f"{loc}, Romania"


def parse_jobs(data: dict, host: str, site: str, company: str, today: date) -> list[RawJob]:
    jobs = []
    for p in data.get("jobPostings", []):
        path = p.get("externalPath") or ""
        m = re.search(r"_([A-Za-z0-9-]+)$", path)
        if not path or not m:
            continue
        loc = (p.get("locationsText") or "").strip()
        multi = not loc or bool(MULTI.match(loc))
        jobs.append(
            RawJob(
                source="workday",
                native_id=f"{host.split('.')[0]}:{m.group(1)}",
                url=f"https://{host}/{site}{path}",
                title=" ".join((p.get("title") or "").split()),
                company=company,
                location=f"Romania ({loc})" if loc and multi else _romanian(loc or "Romania"),
                remote="remote" in loc.lower(),
                extra={
                    "posted_text": p.get("postedOn"),
                    "posted_approx": parse_posted(p.get("postedOn"), today),
                    "multi_location": multi,
                },
            )
        )
    return jobs


def parse_detail(data: dict) -> dict:
    info = data.get("jobPostingInfo") or {}
    locs = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
    ro = [l for l in locs if l and RO_PLACE.search(l)]
    return {
        "description": html_to_text(info.get("jobDescription") or ""),
        "location": _romanian(ro[0]) if ro else None,
        "locations": [l for l in locs if l],
        "posted": info.get("startDate") or None,
        "open": bool(info.get("posted", True)) and info.get("canApply", True) is not False,
    }


def api_url(public_url: str) -> str | None:
    """https://{host}/{site}/job/... (optionally /en-US/{site}/...) → its CXS detail endpoint."""
    u = urlsplit(public_url)
    parts = [p for p in u.path.split("/") if p]
    if parts and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]):
        parts = parts[1:]
    if len(parts) < 3 or parts[1] != "job" or not u.hostname:
        return None
    tenant = u.hostname.split(".")[0]
    return f"https://{u.hostname}/wday/cxs/{tenant}/{parts[0]}/job/{'/'.join(parts[2:])}"


class Workday:
    name = "workday"
    complete = True

    def __init__(self, tenants: list[tuple[str, str, str]] = TENANTS, delay: float = 0.3):
        self.tenants = tenants
        self.delay = delay

    def _post(self, client: httpx.Client, url: str, body: dict) -> dict:
        for attempt in range(3):
            r = client.post(url, json=body, headers={"Accept": "application/json"})
            if r.status_code not in (429, 500, 502, 503, 504):
                break
            time.sleep(10 * self.delay * (attempt + 1))
        r.raise_for_status()
        return r.json()

    def _detail(self, client: httpx.Client, url: str) -> httpx.Response:
        r = client.get(url, headers={"Accept": "application/json"})
        time.sleep(self.delay)
        return r

    def fetch_tenant(self, client: httpx.Client, host: str, site: str, company: str) -> list[RawJob]:
        url = f"https://{host}/wday/cxs/{host.split('.')[0]}/{site}/jobs"
        first = self._post(client, url, {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""})
        if not first.get("facets"):
            raise RuntimeError(f"workday {host}/{site}: response has no facets")
        applied = romania_facets(first["facets"])
        if not applied:
            return []  # the tenant has no Romanian postings right now
        today = date.today()
        jobs: list[RawJob] = []
        total, offset = None, 0
        while total is None or offset < total:
            time.sleep(self.delay)
            data = self._post(
                client, url, {"appliedFacets": applied, "limit": PAGE_SIZE, "offset": offset, "searchText": ""}
            )
            if total is None:
                total = data.get("total") or 0  # only the first page carries the real total
            batch = parse_jobs(data, host, site, company, today)
            if not data.get("jobPostings"):
                break
            jobs += batch
            offset += PAGE_SIZE
        for job in jobs:
            if job.extra["multi_location"] and category(job.title) is not None:
                self._resolve_location(client, job)
        return jobs

    def _resolve_location(self, client: httpx.Client, job: RawJob) -> None:
        r = self._detail(client, api_url(job.url) or "")
        r.raise_for_status()
        info = parse_detail(r.json())
        if info["location"]:
            job.location = info["location"]
            job.remote = job.remote or "remote" in info["location"].lower()
        job.description = info["description"]
        job.posted = info["posted"]
        job.extra["locations"] = info["locations"]

    def fetch(self, client: httpx.Client) -> list[RawJob]:
        found: dict[str, RawJob] = {}
        for host, site, company in self.tenants:
            for job in self.fetch_tenant(client, host, site, company):
                found.setdefault(job.native_id, job)
        return list(found.values())

    def enrich(self, client: httpx.Client, job: RawJob) -> RawJob:
        url = api_url(job.url)
        if not url:
            return job
        r = self._detail(client, url)
        if r.status_code == 200:
            info = parse_detail(r.json())
            job.description = info["description"]
            job.posted = job.posted or info["posted"]
        return job

    def check_open(self, client: httpx.Client, url: str) -> bool | None:
        api = api_url(url)
        if not api:
            return None
        try:
            r = self._detail(client, api)
        except httpx.HTTPError:
            return None
        if r.status_code in (404, 410):
            return False
        if r.status_code != 200:
            return None
        return parse_detail(r.json())["open"]


SOURCE = Workday()
