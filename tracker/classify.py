"""Decide whether a listing is in scope, its category, and where it is."""
from __future__ import annotations

import re
import unicodedata

AML = re.compile(
    r"\b(aml|ctf|kyc|kyb|kyu|cdd(?!\s*\d)|edd|financial crime|fincrime|sanctions?|mlro|money laundering|"
    r"anti.?financial.?crime|transaction monitoring|due dil+igence|customer onboarding|spalar\w*|spălar\w*)\b",
    re.I,
)
DATA_PROTECTION = re.compile(
    r"(data (office )?protection|privacy|\bdpo\b|gdpr|date cu caracter personal|protec[tț]i[ae] datelor)", re.I
)
COMPLIANCE = re.compile(r"(complian|conformitate|regulatory|\bethics?\b|\betic[aăi]\b)", re.I)
FRAUD = re.compile(r"\bfraud\b", re.I)
# Roles that mention compliance but aren't compliance-officer work.
ADJACENT = re.compile(
    r"\b(sox|grc|pci|export|trade compliance|customs|cyber\w*|security|it compliance|engineer\w*|scientist|"
    r"developer|product (owner|manager|marketing|specialist|compliance)|functional owner|quality|qa|gmp|"
    r"pharmacovigilance|regulatory (affairs|reporting|data)|tax|audit\w*|treasury|technical|equipment|hse|"
    r"environmental|sustainability|esg|materials|packaging|procurement|contract compliance|delivery lead|"
    r"tech lead|program manager|data scientist|data analyst|data modeler|it risk|cloud|information security|"
    r"accountant|accounting)\b",
    re.I,
)

CATEGORIES = ["Compliance", "AML / KYC / FinCrime", "Data protection / DPO", "Adjacent"]


def category(title: str) -> str | None:
    """Return the category for a job title, or None when it isn't a compliance role at all."""
    aml, dp, comp = AML.search(title), DATA_PROTECTION.search(title), COMPLIANCE.search(title)
    if not (aml or dp or comp):
        return "Adjacent" if FRAUD.search(title) else None
    if ADJACENT.search(title):
        return "Adjacent"
    if aml:
        return "AML / KYC / FinCrime"
    if dp:
        return "Data protection / DPO"
    return "Compliance"


# Location patterns are matched against an accent-free lowercase copy (Timişoara/Timișoara → timisoara).
BUCHAREST = re.compile(r"bucuresti|bucharest|bukarest|bucarest|ilfov|otopeni|voluntari|pipera|chiajna")
RO_CITIES = {
    "Cluj-Napoca": r"cluj",
    "Iași": r"\biasi\b",
    "Timișoara": r"timisoara|\btimis\b|giarmata",
    "Brașov": r"brasov",
    "Sibiu": r"sibiu",
    "Craiova": r"craiova",
    "Oradea": r"oradea",
    "Constanța": r"constanta",
    "Arad": r"\barad\b",
    "Târgu Mureș": r"targu.?mures",
    "Ploiești": r"ploiesti",
    "Pitești": r"pitesti",
    "Galați": r"galati",
    "Bacău": r"bacau",
    "Suceava": r"suceava",
}
ROMANIA = re.compile(r"romani[ae]|\bro\b")
REMOTE = re.compile(r"remote|home.?based|telecommute|work from home|la distan[tț][aă]|anywhere", re.I)
# Regions a remote role must be open to for a Romania-based candidate to apply.
REMOTE_REGION = re.compile(r"roman|\beu\b|europe|emea|cee|worldwide|anywhere|global", re.I)


def place(location: str, remote_hint: bool = False) -> dict:
    """Normalise a raw location string.

    Returns city ("Bucharest", another Romanian city, "Romania" when no city is
    given, or the raw text), remote, in_romania and remote_ro (remote and open to
    a candidate living in Romania).
    """
    loc = location or ""
    plain = _ascii(loc)
    remote = remote_hint or bool(REMOTE.search(loc))
    if BUCHAREST.search(plain):
        city = "Bucharest"
    else:
        city = next((name for name, rx in RO_CITIES.items() if re.search(rx, plain)), None)
        if city is None:
            city = "Romania" if ROMANIA.search(plain) else (loc.split(",")[0].strip() or "Unknown")
    in_romania = city == "Bucharest" or city in RO_CITIES or city == "Romania"
    remote_ro = remote and (in_romania or bool(REMOTE_REGION.search(loc)))
    return {"city": city, "remote": remote, "in_romania": in_romania, "remote_ro": remote_ro}


def in_scope(location_info: dict) -> bool:
    return location_info["in_romania"] or location_info["remote_ro"]


def should_alert(job: dict) -> bool:
    """Core roles in Bucharest, Romania with no city given, or remote and open to Romania."""
    return job["is_core"] and (job["city"] in ("Bucharest", "Romania") or job["remote_ro"])


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


_COMPANY_NOISE = re.compile(
    r"\b(romania|srl|s r l|sa|s a|ltd|llc|inc|gmbh|plc|the|group|hubs?|global solution centre|"
    r"european business services|ess|gsc|services|bank|banca)\b"
)
_TITLE_NOISE = re.compile(r"\((?:[mfdwx]/?)+\)|\b[mfdwx](?:/[mfdwx])+\b|@.*$|-\s*ing hubs romania")


# Job boards and agencies that repost other employers' roles under their own name.
AGGREGATORS = re.compile(r"hipo|undelucram|jobgether|ejobs|bestjobs|jooble|prohuman|skilld|workster|cross border talents|cbtalents", re.I)


def is_aggregator(company: str) -> bool:
    return bool(AGGREGATORS.search(company or ""))


def title_city_key(title: str, city: str) -> str:
    t = re.sub(r"[^a-z0-9]+", " ", _TITLE_NOISE.sub(" ", _ascii(title)))
    return " ".join(t.split()) + "|" + _ascii(city)


def fingerprint(company: str, title: str, city: str) -> str:
    """Key that treats the same role seen on two sources as one job."""
    c = re.sub(r"[^a-z0-9]+", " ", _COMPANY_NOISE.sub(" ", re.sub(r"[^a-z0-9]+", " ", _ascii(company))))
    t = re.sub(r"[^a-z0-9]+", " ", _TITLE_NOISE.sub(" ", _ascii(title)))
    return "|".join(" ".join(x.split()) for x in (c, t, _ascii(city)))
