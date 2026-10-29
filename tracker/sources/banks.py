"""Careers sites of Romanian banks, insurers and payment firms.

Each site returns its full list of open jobs (any role); the classifier keeps the
compliance ones. Every list is complete, so the source is ``complete`` and any site
error, or a site that suddenly lists nothing, fails the whole run rather than
letting its jobs look closed. ``native_id`` is ``<site>:<site id>``.
"""
from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime
from typing import Callable
from urllib.parse import unquote_plus, urljoin

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .base import CLOSED_PHRASES, RawJob, html_to_text

RO_MONTHS = {m: i for i, m in enumerate(
    ["ianuarie", "februarie", "martie", "aprilie", "mai", "iunie", "iulie", "august", "septembrie", "octombrie",
     "noiembrie", "decembrie"], 1)}


def _text(node) -> str:
    return " ".join(node.text().split()) if node else ""


def _job(site: str, sid: str, url: str, title: str, company: str, location: str, **kw) -> RawJob:
    if "romania" not in location.lower():
        location = f"{location}, Romania" if location else "Romania"
    return RawJob(source="banks", native_id=f"{site}:{sid}", url=url, title=" ".join(title.split()),
                  company=company, location=location, **kw)


def _ro_date(text: str) -> str | None:
    """'29 Octombrie 2026' -> '2026-10-29'."""
    m = re.search(r"(\d{1,2})\s+([a-zăâîșțA-ZĂÂÎȘȚ]+)\s+(\d{4})", text or "")
    month = RO_MONTHS.get(m.group(2).lower()) if m else None
    return f"{m.group(3)}-{month:02d}-{int(m.group(1)):02d}" if month else None


# ---- Libra Internet Bank ----------------------------------------------------

LIBRA = "https://www.librabank.ro/Cariere"


def parse_libra(page: str) -> list[RawJob]:
    jobs = {}
    for a in HTMLParser(page).css("div.card h3.card-title a"):
        href = a.attributes.get("href") or ""
        m = re.match(r"/Cariere/([^/]+)/(\d+)$", href)
        if not m:
            continue
        title = unquote_plus(m.group(1)).strip()
        heading = _text(a)
        where = heading[len(title):].lstrip(" -") if heading.startswith(title) else heading.partition(" - ")[2]
        jobs[m.group(2)] = _job("libra", m.group(2), urljoin(LIBRA, href), title, "Libra Internet Bank", where)
    return list(jobs.values())


def parse_libra_posting(page: str) -> dict:
    tree = HTMLParser(page)
    body = tree.css_first("div.jobDescription div.col-md-7")
    date = next((_text(n) for n in tree.css("div.icon-date")), "")
    return {"description": html_to_text(body.html) if body else "", "posted": _ro_date(date)}


# ---- Signal Iduna -------------------------------------------------------------

SIGNAL = "https://www.signal-iduna.ro/ro/cariere"


def parse_signal(page: str) -> list[RawJob]:
    jobs = []
    for card in HTMLParser(page).css("div.card-explore"):
        link = card.css_first("a[href*='/ro/cariere/']")
        if not link:
            continue
        url = link.attributes["href"]
        dept = _text(card.css_first("p"))
        jobs.append(_job("signal", url.rstrip("/").rsplit("/", 1)[-1], url, _text(card.css_first("h3")),
                         "SIGNAL IDUNA Asigurări", "", extra={"department": dept} if dept else {}))
    return jobs


def parse_signal_posting(page: str) -> dict:
    tree = HTMLParser(page)
    body = tree.css_first("div.career-content")
    posted = None
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text())
        except ValueError:
            continue
        if data.get("@type") == "JobPosting":
            posted = (data.get("datePosted") or "")[:10] or None
    return {"description": html_to_text(body.html) if body else "", "posted": posted}


# ---- NETOPIA Payments -----------------------------------------------------------

NETOPIA = "https://netopia-payments.com/joburi/"


def parse_netopia(page: str) -> list[RawJob]:
    jobs = []
    for item in HTMLParser(page).css("div.job-item"):
        link = item.css_first("h4 a")
        if not link:
            continue
        url = link.attributes["href"]
        body = item.css_first(".hidden-content")
        dept = _text(item.css_first(".job-tag"))
        jobs.append(_job("netopia", url.rstrip("/").rsplit("/", 1)[-1], url, html.unescape(_text(link)),
                         "NETOPIA Payments", "", description=html_to_text(body.html) if body else "",
                         extra={"department": dept} if dept else {}))
    return jobs


# ---- Mingle ATS (Raiffeisen's careers site, CEC's embedded board) -------------

RAIFFEISEN = "https://cariere.raiffeisen.ro/jobs"
CEC_API = "https://mingle.ro/api/boards/careers-page/jobs"


def parse_raiffeisen(page: str) -> tuple[list[RawJob], int]:
    """Jobs on one list page and the number of the last page."""
    m = re.search(r':jobs="(\[.*?\])"', page, re.S)
    p = re.search(r':pagination="(.*?)"', page, re.S)
    if not m or not p:
        raise ValueError("Raiffeisen: job data not found on page")
    jobs = []
    for j in json.loads(html.unescape(m.group(1))):
        if j.get("closed") or not j.get("active", True):
            continue
        cities = ", ".join(c["name"] for c in j.get("city") or [])
        jobs.append(_job(
            "raiffeisen", str(j["id"]), j["url"], j["title"], (j.get("company") or {}).get("name") or "Raiffeisen Bank",
            cities, posted=((j.get("board") or {}).get("created_at") or "")[:10] or None,
            description=html_to_text((j.get("description") or "") + (j.get("experience_needed") or "")),
        ))
    return jobs, int(json.loads(html.unescape(p.group(1))).get("last") or 1)


def parse_cec(data: dict) -> list[RawJob]:
    jobs = []
    for j in data["data"]["results"]:
        cities = ", ".join(loc["label"] for loc in j.get("locations") or [])
        jobs.append(_job("cec", j["uid"], f"https://cec.mingle.ro/ro/apply/{j['uid']}", j["title"], "CEC Bank",
                         cities, description=html_to_text(j.get("description") or "")))
    return jobs


# ---- SuccessFactors career sites (BCR on Erste's site, Groupama) ----------------

BCR = "https://erstegroup-careers.com/bcr/search/"
GROUPAMA = "https://cariere.groupama.ro/search/"


def parse_successfactors(page: str, site: str, company: str, base: str) -> tuple[list[RawJob], int]:
    """Jobs on one search page and the total count from the pagination label."""
    tree = HTMLParser(page)
    jobs = []
    for row in tree.css("tr.data-row"):
        link = row.css_first("a.jobTitle-link")
        m = re.search(r"/(\d+)/?$", link.attributes.get("href") or "") if link else None
        if not m:
            continue
        where = _text(row.css_first("td.colLocation .jobLocation") or row.css_first("td.colShifttype .jobShifttype"))
        jobs.append(_job(site, m.group(1), urljoin(base, link.attributes["href"]), _text(link), company, where))
    label = tree.css_first(".paginationLabel")
    total = re.findall(r"\d+", _text(label))
    return jobs, int(total[-1]) if total else len(jobs)


def parse_successfactors_posting(page: str) -> dict:
    tree = HTMLParser(page)
    body = tree.css_first(".jobdescription")
    posted = tree.css_first('meta[itemprop="datePosted"]')
    iso = None
    if posted:
        try:
            iso = datetime.strptime(posted.attributes.get("content", ""), "%a %b %d %H:%M:%S %Z %Y").date().isoformat()
        except ValueError:
            pass
    return {"description": html_to_text(body.html) if body else "", "posted": iso}


# ---- UniCredit (Avature) --------------------------------------------------------

UNICREDIT = "https://careers.unicredit.eu/en_GB/jobsuche/SearchJobs"
UNICREDIT_PAGE = 15


def parse_unicredit(page: str) -> tuple[list[RawJob], int]:
    """Romanian jobs on one search page and the total result count."""
    tree = HTMLParser(page)
    jobs = []
    for art in tree.css("article"):
        link = art.css_first("h3 a")
        if not link:
            continue
        info = [_text(s) for s in art.css(".article__header__text__subtitle span")]
        if not info or info[0] != "Romania":
            continue
        url = link.attributes["href"]
        jobs.append(_job("unicredit", url.rstrip("/").rsplit("/", 1)[-1], url, _text(link), "UniCredit Bank", "",
                         extra={"area": info[1]} if len(info) > 1 else {}))
    total = tree.css(".list-controls__text__legend .legend--bold")
    return jobs, int(_text(total[-1])) if total else 0


def parse_unicredit_posting(page: str) -> dict:
    fields, text = {}, []
    for f in HTMLParser(page).css(".article__content__view__field"):
        label, value = f.css_first(".article__content__view__field__label"), f.css_first(".article__content__view__field__value")
        if label:
            fields[_text(label)] = _text(value)
        elif value:
            text.append(html_to_text(value.html))
    return {"description": "\n\n".join(text), "city": fields.get("City")}


# ---- TBI Bank (Recruitee) -------------------------------------------------------

TBI = "https://tbibankro.recruitee.com/api/offers/"


def parse_tbi(data: dict) -> list[RawJob]:
    jobs = []
    for o in data["offers"]:
        if o.get("status") != "published":
            continue
        remote = bool(o.get("remote"))
        where = o.get("location") or ""
        if remote and "romania" not in where.lower():
            where = f"Remote, {o.get('city') or ''}".rstrip(", ")
        jobs.append(_job("tbi", str(o["id"]), o["careers_url"], o["title"], o.get("company_name") or "TBI Bank", where,
                         posted=(o.get("published_at") or "")[:10] or None, remote=remote,
                         description=html_to_text((o.get("description") or "") + (o.get("requirements") or ""))))
    return jobs


# ---- the source -----------------------------------------------------------------

class Banks:
    name = "banks"
    complete = True

    def __init__(self, delay: float = 1.0):
        self.delay = delay

    def _get(self, client: httpx.Client, url: str, **params) -> httpx.Response:
        r = client.get(url, params=params or None)
        r.raise_for_status()
        time.sleep(self.delay)
        return r

    def libra(self, client: httpx.Client) -> list[RawJob]:
        return parse_libra(self._get(client, LIBRA).text)

    def signal(self, client: httpx.Client) -> list[RawJob]:
        return parse_signal(self._get(client, SIGNAL).text)

    def netopia(self, client: httpx.Client) -> list[RawJob]:
        return parse_netopia(self._get(client, NETOPIA).text)

    def raiffeisen(self, client: httpx.Client) -> list[RawJob]:
        jobs, last = parse_raiffeisen(self._get(client, RAIFFEISEN).text)
        for page in range(2, min(last, 10) + 1):
            jobs += parse_raiffeisen(self._get(client, RAIFFEISEN, page=page).text)[0]
        return jobs

    def cec(self, client: httpx.Client) -> list[RawJob]:
        data = self._get(client, CEC_API, company="cec", page=0, pageSize=200, sort="date_desc").json()
        if data["data"]["pagination"].get("hasNext"):
            raise RuntimeError("banks/cec: more than one page of jobs, raise pageSize")
        return parse_cec(data)

    def _successfactors(self, client: httpx.Client, url: str, site: str, company: str, **params) -> list[RawJob]:
        jobs: dict[str, RawJob] = {}
        total = None
        while total is None or len(jobs) < total:
            batch, total = parse_successfactors(self._get(client, url, q="", startrow=len(jobs), **params).text,
                                                site, company, url)
            new = {j.native_id: j for j in batch if j.native_id not in jobs}
            if not new:
                raise RuntimeError(f"banks/{site}: got {len(jobs)} of {total} jobs")
            jobs.update(new)
        return list(jobs.values())

    def bcr(self, client: httpx.Client) -> list[RawJob]:
        return self._successfactors(client, BCR, "bcr", "BCR", locale="ro_RO")

    def groupama(self, client: httpx.Client) -> list[RawJob]:
        return self._successfactors(client, GROUPAMA, "groupama", "Groupama Asigurări")

    def unicredit(self, client: httpx.Client) -> list[RawJob]:
        jobs: dict[str, RawJob] = {}
        for offset in range(0, 20 * UNICREDIT_PAGE, UNICREDIT_PAGE):
            batch, total = parse_unicredit(self._get(client, UNICREDIT, search="Romania",
                                                     jobRecordsPerPage=UNICREDIT_PAGE, jobOffset=offset).text)
            jobs.update((j.native_id, j) for j in batch)
            if offset + UNICREDIT_PAGE >= total:
                break
        else:
            raise RuntimeError("banks/unicredit: too many pages")
        # Free-text "Romania" can also match other countries' jobs; those are filtered out above.
        if len(jobs) < total * 0.9:
            raise RuntimeError(f"banks/unicredit: got {len(jobs)} Romanian jobs of {total} results")
        return list(jobs.values())

    def tbi(self, client: httpx.Client) -> list[RawJob]:
        return parse_tbi(self._get(client, TBI).json())

    def sites(self) -> list[tuple[str, Callable[[httpx.Client], list[RawJob]]]]:
        return [("libra", self.libra), ("signal", self.signal), ("netopia", self.netopia),
                ("raiffeisen", self.raiffeisen), ("cec", self.cec), ("bcr", self.bcr), ("groupama", self.groupama),
                ("unicredit", self.unicredit), ("tbi", self.tbi)]

    def fetch(self, client: httpx.Client) -> list[RawJob]:
        found: dict[str, RawJob] = {}
        for site, fetch_site in self.sites():
            jobs = fetch_site(client)
            if not jobs:  # a layout change would otherwise read as "every job closed"
                raise RuntimeError(f"banks/{site}: no jobs parsed")
            for job in jobs:
                found.setdefault(job.native_id, job)
        return list(found.values())

    def enrich(self, client: httpx.Client, job: RawJob) -> RawJob:
        site = job.native_id.split(":", 1)[0]
        parser = {"libra": parse_libra_posting, "signal": parse_signal_posting, "bcr": parse_successfactors_posting,
                  "groupama": parse_successfactors_posting, "unicredit": parse_unicredit_posting}.get(site)
        if parser is None:
            return job
        r = client.get(job.url)
        if r.status_code != 200:
            return job
        info = parser(r.text)
        job.description = info["description"] or job.description
        if info.get("posted") and not job.posted:
            job.posted = info["posted"]
        if info.get("city"):
            job.extra["city"] = info["city"]
        return job

    def check_open(self, client: httpx.Client, url: str) -> bool | None:
        m = re.match(r"https://cec\.mingle\.ro/ro/apply/([\w-]+)", url)
        try:
            # The Mingle page is a client-side app that always answers 200; its API 404s.
            r = client.get(f"{CEC_API}/{m.group(1)}", params={"company": "cec"}) if m else client.get(url)
        except httpx.HTTPError:
            return None
        final = str(r.url)
        # Libra redirects a removed job to the list; SuccessFactors and Avature to an error page.
        if "/errorpage" in final or final.endswith("/jobsuche/Error") or (
            url.startswith(LIBRA + "/") and final.rstrip("/").lower() == LIBRA.lower()
        ):
            return False
        if r.status_code in (404, 410):
            return False
        if r.status_code != 200:
            return None
        return not any(p in r.text.lower() for p in CLOSED_PHRASES)


SOURCE = Banks()
