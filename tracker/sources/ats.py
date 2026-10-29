"""Public applicant-tracking-system job boards: Greenhouse, Lever, Ashby,
SmartRecruiters, Teamtailor, plus Workable's cross-company job search.

Per-company boards return every open job of the company; we keep the ones in
Romania, or remote and open to Romania/EU/EMEA/Europe. SmartRecruiters is asked
for ``country=ro`` directly and Workable is searched by keyword with
``location=Romania``; both are paginated to the end. Every board and query is
fetched in full and any HTTP failure raises, so the source is ``complete``.
"""
from __future__ import annotations

import html
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from .base import KEYWORDS, RawJob, generic_check_open, html_to_text

# Board tokens. Romania-relevant listings on 2026-10-29 are in the comments.
GREENHOUSE = [
    "okx",  # remote EMEA compliance roles
    "ebury",  # Bucharest, Cluj
    "bitpanda",  # Bucharest
    "emag",  # Bucharest
    "stripe",  # Bucharest + "Romania - Remote"
    "bybit",
    "complyadvantage",  # Cluj office
    "payoneer",
    "sumup",
    "veriff",
]
LEVER: list[tuple[str, bool]] = [  # (company, on the EU instance)
    ("certik", False),
    ("swissborg", False),
    ("viseven", False),  # Bucharest
    ("moonpay", False),  # "Romania - Remote"
    ("crypto", False),  # Crypto.com
    ("coinspaid", True),
]
ASHBY = [
    "uipath",  # Bucharest
    "trading212",  # Romania
    "elliptic",
    "ledger",
]
SMARTRECRUITERS = [
    "AUMOVIO",
    "DeliveryHero",  # Glovo / foodpanda Romania
    "Endava",
    "BoschGroup",
    "Continental",
    "Playtech",
    "Evolution",
    "Sutherland",
    "Abbvie",
]
TEAMTAILOR = [("worksterjobs", "Workster")]  # recruiter for AML/KYC centres in Bucharest
WORKABLE_QUERIES = list(dict.fromkeys([*KEYWORDS, "privacy", "GDPR"]))

RO_PLACE = re.compile(
    r"roman|\brou?\b|bucharest|bucure|cluj|ia[sș]i\b|timi[sș]oara|bra[sș]ov|sibiu|craiova|oradea|constan[tț]a|"
    r"ploie[sș]ti|pite[sș]ti|t[aâ]rgu.?mure",
    re.I,
)
RO_CITY = re.compile(
    r"\b(bucharest|bucure[sș]ti|cluj(-napoca)?|ia[sș]i|timi[sș]oara|bra[sș]ov|sibiu|craiova|oradea|constan[tț]a)\b", re.I
)
REMOTE = re.compile(r"remote|anywhere|home.?based|telecommute|work from home", re.I)
REGION = re.compile(r"\b(eu|emea|europe\w*|cee|worldwide|global|anywhere)\b", re.I)
REGION_ONLY = re.compile(r"^\s*(eu|emea|europe|european union|cee)\s*$", re.I)


def place(locations: list[str], remote_hint: bool = False) -> tuple[str, bool] | None:
    """(location, remote) when a listing is in Romania or remote and open to it, else None."""
    locs = [" ".join(l.split()) for l in locations if l and l.strip()]
    for loc in locs:
        if RO_PLACE.search(loc):
            remote = remote_hint or bool(REMOTE.search(loc))
            loc = loc if re.search(r"roman", loc, re.I) else f"{loc}, Romania"
            return (f"{loc} (Remote)" if remote and not REMOTE.search(loc) else loc), remote
    for loc in locs:
        if REGION.search(loc) and (remote_hint or REMOTE.search(loc) or REGION_ONLY.match(loc)):
            return (loc if REMOTE.search(loc) else f"Remote - {loc}"), True
    return None


def _day(value: str | int | None) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):  # epoch milliseconds
        return datetime.fromtimestamp(value / 1000, timezone.utc).date().isoformat()
    return str(value)[:10]


def parse_greenhouse(data: dict, board: str) -> list[RawJob]:
    jobs = []
    for j in data.get("jobs", []):
        where = place(((j.get("location") or {}).get("name") or "").split(";"))
        if not where:
            continue
        jobs.append(
            RawJob(
                source="ats",
                native_id=f"greenhouse:{board}:{j['id']}",
                url=j.get("absolute_url") or f"https://job-boards.greenhouse.io/{board}/jobs/{j['id']}",
                title=" ".join(j["title"].split()),
                company=j.get("company_name") or board,
                location=where[0],
                remote=where[1],
                posted=_day(j.get("first_published")),
                description=html_to_text(html.unescape(j.get("content") or "")),
            )
        )
    return jobs


def parse_lever(data: list, company: str, eu: bool = False) -> list[RawJob]:
    jobs = []
    for j in data:
        cats = j.get("categories") or {}
        locs = cats.get("allLocations") or [cats.get("location") or ""]
        where = place(locs, remote_hint=j.get("workplaceType") == "remote")
        if not where:
            continue
        parts = [j.get("description") or ""]
        parts += [f"<h3>{l.get('text', '')}</h3><ul>{l.get('content', '')}</ul>" for l in j.get("lists") or []]
        parts.append(j.get("additional") or "")
        jobs.append(
            RawJob(
                source="ats",
                native_id=f"lever:{company}:{j['id']}",
                url=j.get("hostedUrl") or f"https://jobs.{'eu.' if eu else ''}lever.co/{company}/{j['id']}",
                title=" ".join(j["text"].split()),
                company=company,
                location=where[0],
                remote=where[1],
                posted=_day(j.get("createdAt")),
                description=html_to_text("".join(parts)),
                extra={"team": cats.get("team"), "commitment": cats.get("commitment")},
            )
        )
    return jobs


def parse_ashby(data: dict, board: str, company: str | None = None) -> list[RawJob]:
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location") or "" for s in j.get("secondaryLocations") or []]
        country = (((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry")) or ""
        if country and RO_PLACE.search(country) and not any(RO_PLACE.search(l) for l in locs):
            locs.insert(0, f"{locs[0]}, {country}" if locs[0] else country)
        where = place(locs, remote_hint=j.get("workplaceType") == "Remote")
        if not where:
            continue
        jobs.append(
            RawJob(
                source="ats",
                native_id=f"ashby:{board}:{j['id']}",
                url=j.get("jobUrl") or f"https://jobs.ashbyhq.com/{board}/{j['id']}",
                title=" ".join(j["title"].split()),
                company=company or board,
                location=where[0],
                remote=where[1],
                posted=_day(j.get("publishedAt")),
                description=html_to_text(j.get("descriptionHtml") or ""),
            )
        )
    return jobs


def parse_smartrecruiters(data: dict) -> list[RawJob]:
    jobs = []
    for j in data.get("content", []):
        loc = j.get("location") or {}
        if (loc.get("country") or "").lower() != "ro":
            continue
        remote = bool(loc.get("remote"))
        full = loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), "Romania") if x)
        where = place([full], remote_hint=remote) or ("Romania (Remote)" if remote else "Romania", remote)
        company = j.get("company") or {}
        cid = company.get("identifier") or ""
        jobs.append(
            RawJob(
                source="ats",
                native_id=f"smartrecruiters:{cid}:{j['id']}",
                url=f"https://jobs.smartrecruiters.com/{cid}/{j['id']}",
                title=" ".join(j["name"].split()),
                company=company.get("name") or cid,
                location=where[0],
                remote=where[1],
                posted=_day(j.get("releasedDate")),
            )
        )
    return jobs


def parse_smartrecruiters_posting(data: dict) -> str:
    sections = (data.get("jobAd") or {}).get("sections") or {}
    keys = ("jobDescription", "qualifications", "additionalInformation")
    return html_to_text("".join(f"<h3>{sections[k].get('title', '')}</h3>{sections[k].get('text', '')}"
                                for k in keys if sections.get(k)))


def parse_workable(data: dict) -> list[RawJob]:
    jobs = []
    for j in data.get("jobs", []):
        loc = j.get("location") or {}
        if (loc.get("countryName") or "").lower() != "romania":
            continue
        remote = j.get("workplace") == "remote" or "TELECOMMUTE" in (j.get("locations") or [])
        city = loc.get("city") or ""
        where = f"{city}, Romania" if city else "Romania"
        if remote:
            where = f"{where} (Remote)" if city else "Remote (Romania)"
        jobs.append(
            RawJob(
                source="ats",
                native_id=f"workable:{j['id']}",
                url=j.get("url") or "",
                title=" ".join(j["title"].split()),
                company=(j.get("company") or {}).get("title") or "",
                location=where,
                remote=remote,
                posted=_day(j.get("created")),
                description=html_to_text((j.get("description") or "") + (j.get("requirementsSection") or "")),
            )
        )
    return jobs


def parse_teamtailor(data: dict, sub: str, company: str) -> list[RawJob]:
    jobs = []
    for it in data.get("items", []):
        m = re.search(r"/jobs/(\d+)", it.get("url") or "")
        if not m:
            continue
        posting = it.get("_jobposting") or {}
        locs = []
        for jl in posting.get("jobLocation") or []:
            addr = jl.get("address") or {}
            country = addr.get("addressCountry") or ""
            country = "Romania" if country.upper() in ("RO", "ROU") else country
            locs.append(", ".join(x for x in (addr.get("addressLocality"), country) if x))
        if not locs:  # no structured location: fall back to a city in the title ("... - Bucharest")
            m_city = RO_CITY.search(it.get("title") or "")
            locs = [f"{m_city.group(0).title()}, Romania"] if m_city else []
        where = place(locs, remote_hint=posting.get("jobLocationType") == "TELECOMMUTE")
        if not where:
            continue
        jobs.append(
            RawJob(
                source="ats",
                native_id=f"teamtailor:{sub}:{m.group(1)}",
                url=it["url"],
                title=" ".join(it["title"].split()),
                company=company,
                location=where[0],
                remote=where[1],
                posted=_day(it.get("date_published") or posting.get("datePosted")),
                description=html_to_text(it.get("content_html") or posting.get("description") or ""),
            )
        )
    return jobs


class ATS:
    name = "ats"
    complete = True

    def __init__(self, delay: float = 0.3, max_pages: int = 25):
        self.delay = delay
        self.max_pages = max_pages
        self.counts: dict[str, int] = {}

    def _get(self, client: httpx.Client, url: str, **params) -> httpx.Response:
        for attempt in range(3):
            r = client.get(url, params=params or None)
            if r.status_code not in (429, 500, 502, 503, 504):
                break
            time.sleep(10 * self.delay * (attempt + 1))
        r.raise_for_status()
        time.sleep(self.delay)
        return r

    def _greenhouse(self, client: httpx.Client, board: str) -> list[RawJob]:
        r = self._get(client, f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", content="true")
        return parse_greenhouse(r.json(), board)

    def _lever(self, client: httpx.Client, company: str, eu: bool) -> list[RawJob]:
        r = self._get(client, f"https://api.{'eu.' if eu else ''}lever.co/v0/postings/{company}", mode="json")
        return parse_lever(r.json(), company, eu)

    def _ashby(self, client: httpx.Client, board: str) -> list[RawJob]:
        r = self._get(client, f"https://api.ashbyhq.com/posting-api/job-board/{board}", includeCompensation="false")
        return parse_ashby(r.json(), board)

    def _smartrecruiters(self, client: httpx.Client, company: str) -> list[RawJob]:
        jobs: list[RawJob] = []
        for page in range(self.max_pages):
            data = self._get(
                client, f"https://api.smartrecruiters.com/v1/companies/{company}/postings",
                country="ro", limit=100, offset=page * 100,
            ).json()
            jobs += parse_smartrecruiters(data)
            if (page + 1) * 100 >= (data.get("totalFound") or 0):
                return jobs
        raise RuntimeError(f"smartrecruiters {company}: more than {self.max_pages} pages")

    def _workable(self, client: httpx.Client, query: str) -> list[RawJob]:
        jobs: list[RawJob] = []
        token = None
        for _ in range(self.max_pages):
            params = {"query": query, "location": "Romania"} | ({"pageToken": token} if token else {})
            data = self._get(client, "https://jobs.workable.com/api/v1/jobs", **params).json()
            jobs += parse_workable(data)
            token = data.get("nextPageToken")
            if not token or not data.get("jobs"):
                return jobs
        raise RuntimeError(f"workable {query!r}: more than {self.max_pages} pages")

    def _teamtailor(self, client: httpx.Client, sub: str, company: str) -> list[RawJob]:
        jobs: list[RawJob] = []
        url: str | None = f"https://{sub}.teamtailor.com/jobs.json?per_page=100"
        for _ in range(self.max_pages):
            data = self._get(client, url).json()
            jobs += parse_teamtailor(data, sub, company)
            url = data.get("next_url")
            if not url or not data.get("items"):
                return jobs
        raise RuntimeError(f"teamtailor {sub}: more than {self.max_pages} pages")

    def boards(self) -> list[tuple[str, callable]]:
        return (
            [(f"greenhouse:{b}", lambda c, b=b: self._greenhouse(c, b)) for b in GREENHOUSE]
            + [(f"lever:{co}", lambda c, co=co, eu=eu: self._lever(c, co, eu)) for co, eu in LEVER]
            + [(f"ashby:{b}", lambda c, b=b: self._ashby(c, b)) for b in ASHBY]
            + [(f"smartrecruiters:{co}", lambda c, co=co: self._smartrecruiters(c, co)) for co in SMARTRECRUITERS]
            + [(f"teamtailor:{s}", lambda c, s=s, n=n: self._teamtailor(c, s, n)) for s, n in TEAMTAILOR]
            + [(f"workable:{q}", lambda c, q=q: self._workable(c, q)) for q in WORKABLE_QUERIES]
        )

    def fetch(self, client: httpx.Client) -> list[RawJob]:
        found: dict[str, RawJob] = {}
        self.counts = {}
        for label, fetch in self.boards():
            batch = fetch(client)
            self.counts[label] = len(batch)
            for job in batch:
                found.setdefault(job.native_id, job)
        return list(found.values())

    def enrich(self, client: httpx.Client, job: RawJob) -> RawJob:
        kind, _, rest = job.native_id.partition(":")
        if kind == "smartrecruiters":
            company, _, pid = rest.partition(":")
            r = client.get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{pid}")
            if r.status_code == 200:
                job.description = parse_smartrecruiters_posting(r.json())
        return job

    def check_open(self, client: httpx.Client, url: str) -> bool | None:
        api = api_url(url)
        if api is None:
            return generic_check_open(client, url)
        try:
            r = client.get(api)
        except httpx.HTTPError:
            return None
        if r.status_code in (404, 410):
            return False
        if r.status_code != 200:
            return None
        if "smartrecruiters" in api:
            return r.json().get("active", True) is not False
        return True


def api_url(url: str) -> str | None:
    """The JSON endpoint for one listing on boards whose public pages don't 404 reliably."""
    u = urlsplit(url)
    parts = [p for p in u.path.split("/") if p]
    host = u.hostname or ""
    if host in ("job-boards.greenhouse.io", "boards.greenhouse.io") and len(parts) >= 3 and parts[1] == "jobs":
        return f"https://boards-api.greenhouse.io/v1/boards/{parts[0]}/jobs/{parts[2]}"
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and len(parts) >= 2:
        return f"https://api.{'eu.' if '.eu.' in host else ''}lever.co/v0/postings/{parts[0]}/{parts[1]}"
    if host == "jobs.smartrecruiters.com" and len(parts) >= 2:
        pid = parts[1].split("-")[0]
        return f"https://api.smartrecruiters.com/v1/companies/{parts[0]}/postings/{pid}"
    return None


SOURCE = ATS()
