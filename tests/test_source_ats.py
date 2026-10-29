import json
from pathlib import Path

import httpx
import pytest

from tracker.sources import ats

FIX = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIX / name).read_text())


@pytest.mark.parametrize(
    "locs,hint,expected",
    [
        (["Bucharest"], False, ("Bucharest, Romania", False)),
        (["București, Bucharest, Romania"], False, ("București, Bucharest, Romania", False)),
        (["Poland - Remote OR Romania - Remote"], False, ("Poland - Remote OR Romania - Remote", True)),
        (["Bucharest"], True, ("Bucharest, Romania (Remote)", True)),
        (["EMEA"], False, ("Remote - EMEA", True)),
        (["Remote Roles - EMEA"], False, ("Remote Roles - EMEA", True)),
        (["Europe / Asia"], True, ("Remote - Europe / Asia", True)),
        (["Remote - Europe Timezone"], False, ("Remote - Europe Timezone", True)),
        (["US / Remote"], True, None),
        (["Poland (Remote)"], False, None),
        (["Singapore, Singapore"], False, None),
        (["Global"], False, None),  # a region name alone is only remote for EU/EMEA/Europe
        (["Sliema, Malta", "Remote Roles - EMEA"], False, ("Remote Roles - EMEA", True)),
    ],
)
def test_place(locs, hint, expected):
    assert ats.place(locs, hint) == expected


def test_parse_greenhouse_keeps_remote_emea_only():
    jobs = ats.parse_greenhouse(load("ats_greenhouse_okx.json"), "okx")
    assert len(jobs) == 2
    j = jobs[0]
    assert j.native_id.startswith("greenhouse:okx:") and j.remote
    assert j.location in ("Remote - EMEA", "Remote Roles - EMEA")
    assert j.company == "OKX" and j.url.startswith("https://job-boards.greenhouse.io/okx/jobs/")
    assert j.posted and len(j.posted) == 10
    assert j.description and "<" not in j.description and "&lt;" not in j.description


def test_parse_lever():
    jobs = ats.parse_lever(load("ats_lever.json"), "viseven")
    assert [(j.title, j.location, j.remote) for j in jobs] == [
        ("Compliance and Legal Counsel", "Bucharest, Romania (Remote)", True)
    ]
    j = jobs[0]
    assert j.native_id.startswith("lever:viseven:") and j.url.startswith("https://jobs.lever.co/viseven/")
    assert j.posted and j.description


def test_parse_ashby():
    jobs = ats.parse_ashby(load("ats_ashby_uipath.json"), "uipath", "UiPath")
    assert len(jobs) == 1
    j = jobs[0]
    assert j.location == "Bucharest, Romania" and not j.remote and j.company == "UiPath"
    assert j.native_id.startswith("ashby:uipath:") and j.description


def test_parse_smartrecruiters_and_posting():
    jobs = ats.parse_smartrecruiters(load("ats_smartrecruiters_list.json"))
    assert len(jobs) == 2
    j = jobs[0]
    assert "Compliance" in j.title and "Romania" in j.location and j.company == "Aumovio"
    assert j.native_id.startswith("smartrecruiters:Aumovio:")  # identifier as the API spells it
    assert j.url == f"https://jobs.smartrecruiters.com/Aumovio/{j.native_id.rsplit(':', 1)[1]}"
    assert j.description == ""
    text = ats.parse_smartrecruiters_posting(load("ats_smartrecruiters_posting.json"))
    assert text and "<" not in text


def test_parse_workable():
    jobs = ats.parse_workable(load("ats_workable.json"))
    by_title = {j.title: j for j in jobs}
    remote = by_title["Global Legal Associate: Fintech&Compliance"]
    assert remote.location == "Remote (Romania)" and remote.remote and remote.company == "RemoFirst"
    onsite = by_title["French Anti-Financial Crime Analyst"]
    assert onsite.location == "Bucharest, Romania" and not onsite.remote
    assert all(j.native_id.startswith("workable:") and j.url.startswith("https://jobs.workable.com/") for j in jobs)


def test_parse_teamtailor():
    jobs = ats.parse_teamtailor(load("ats_teamtailor.json"), "worksterjobs", "Workster")
    assert [(j.title, j.location) for j in jobs] == [
        ("French-speaking Anti-Financial Crime Analyst - Bucharest", "Bucharest, Romania"),
        ("AV Specialist (Igaming) Bucharest", "Bucharest, Romania"),  # city taken from the title
    ]
    assert jobs[0].native_id == "teamtailor:worksterjobs:6673103"
    assert jobs[0].posted and jobs[0].description


@pytest.mark.parametrize(
    "url,api",
    [
        ("https://job-boards.greenhouse.io/okx/jobs/123", "https://boards-api.greenhouse.io/v1/boards/okx/jobs/123"),
        ("https://jobs.lever.co/certik/abc-1", "https://api.lever.co/v0/postings/certik/abc-1"),
        ("https://jobs.eu.lever.co/coinspaid/abc-1", "https://api.eu.lever.co/v0/postings/coinspaid/abc-1"),
        (
            "https://jobs.smartrecruiters.com/AUMOVIO/744000-compliance-officer",
            "https://api.smartrecruiters.com/v1/companies/AUMOVIO/postings/744000",
        ),
        ("https://teams.emag.ro/jobs/?gh_jid=1", None),
    ],
)
def test_api_url(url, api):
    assert ats.api_url(url) == api


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_check_open():
    src = ats.ATS(delay=0)
    url = "https://job-boards.greenhouse.io/okx/jobs/1"
    assert src.check_open(_client(lambda r: httpx.Response(404)), url) is False
    assert src.check_open(_client(lambda r: httpx.Response(200, json={})), url) is True
    assert src.check_open(_client(lambda r: httpx.Response(502)), url) is None
    sr = "https://jobs.smartrecruiters.com/AUMOVIO/1"
    assert src.check_open(_client(lambda r: httpx.Response(200, json={"active": False})), sr) is False


def test_enrich_smartrecruiters():
    posting = load("ats_smartrecruiters_posting.json")
    job = ats.parse_smartrecruiters(load("ats_smartrecruiters_list.json"))[0]
    ats.ATS(delay=0).enrich(_client(lambda r: httpx.Response(200, json=posting)), job)
    assert job.description


def test_failures_raise_and_pagination():
    src = ats.ATS(delay=0)
    with pytest.raises(httpx.HTTPStatusError):
        src._greenhouse(_client(lambda r: httpx.Response(404)), "gone")

    page = load("ats_workable.json")
    seen = []

    def workable(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("pageToken")
        seen.append(token)
        return httpx.Response(200, json={**page, "nextPageToken": None if token else "t2"})

    jobs = src._workable(_client(workable), "AML")
    assert seen == [None, "t2"] and len(jobs) == 2 * len(ats.parse_workable(page))

    def endless(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={**page, "nextPageToken": "again"})

    with pytest.raises(RuntimeError):
        ats.ATS(delay=0, max_pages=3)._workable(_client(endless), "AML")
