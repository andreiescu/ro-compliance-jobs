import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from tracker.sources import workday

FIX = Path(__file__).parent / "fixtures"
TODAY = date(2026, 10, 29)


def load(name: str) -> dict:
    return json.loads((FIX / name).read_text())


def test_romania_facets_prefers_country():
    applied = workday.romania_facets(load("workday_facets_accenture.json")["facets"])
    assert applied == {"locationCountry": [workday.ROMANIA_ID]}


def test_romania_facets_falls_back_to_locations():
    applied = workday.romania_facets(load("workday_facets_ing.json")["facets"])
    assert list(applied) == ["locations"]
    assert "1df242ae89a610013fd6c054d6600000" in applied["locations"]  # Bucharest - Dacia One


def test_romania_facets_empty_when_no_romanian_values():
    facets = [{"facetParameter": "locations", "values": [{"descriptor": "Berlin", "id": "x", "count": 3}]}]
    assert workday.romania_facets(facets) == {}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Posted Today", "2026-10-29"),
        ("Posted Yesterday", "2026-10-28"),
        ("Posted 6 Days Ago", "2026-10-23"),
        ("Posted 30+ Days Ago", None),
        (None, None),
    ],
)
def test_parse_posted(text, expected):
    assert workday.parse_posted(text, TODAY) == expected


def test_parse_jobs():
    jobs = workday.parse_jobs(
        load("workday_jobs_iqvia.json"), "iqvia.wd1.myworkdayjobs.com", "IQVIA", "IQVIA", TODAY
    )
    assert len(jobs) == 5
    multi, single, no_loc = jobs[0], jobs[2], jobs[4]
    assert multi.native_id == "iqvia:R1576948"
    assert multi.url == (
        "https://iqvia.wd1.myworkdayjobs.com/IQVIA/job/Kaunas-Lithuania/"
        "Medical-Information-Specialist-with-Lithuanian--English-and-Additional-Language_R1576948"
    )
    assert multi.extra["multi_location"] and multi.location.startswith("Romania (")
    assert single.location == "Bucuresti, Romania" and not single.extra["multi_location"]
    assert no_loc.location == "Romania" and no_loc.extra["multi_location"]
    assert all(j.posted is None and j.source == "workday" for j in jobs)
    assert no_loc.extra["posted_approx"] is None  # "30+ Days Ago"


def test_parse_detail():
    info = workday.parse_detail(load("workday_detail_iqvia.json"))
    assert info["location"] == "Bucuresti, Romania"
    assert info["posted"] == "2026-10-29"
    assert info["open"] is True
    assert "Medical Information" in info["description"] and "<p>" not in info["description"]


@pytest.mark.parametrize(
    "url,api",
    [
        (
            "https://ing.wd3.myworkdayjobs.com/ICSGBLCOR/job/Bucharest/Data-Expert_R123",
            "https://ing.wd3.myworkdayjobs.com/wday/cxs/ing/ICSGBLCOR/job/Bucharest/Data-Expert_R123",
        ),
        (
            "https://ing.wd3.myworkdayjobs.com/en-US/ICSGBLCOR/job/Bucharest/Data-Expert_R123",
            "https://ing.wd3.myworkdayjobs.com/wday/cxs/ing/ICSGBLCOR/job/Bucharest/Data-Expert_R123",
        ),
        ("https://example.com/careers", None),
    ],
)
def test_api_url(url, api):
    assert workday.api_url(url) == api


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_check_open_statuses():
    src = workday.Workday(delay=0)
    url = "https://ing.wd3.myworkdayjobs.com/ICSGBLCOR/job/Bucharest/X_R1"
    assert src.check_open(_client(lambda r: httpx.Response(404, json={})), url) is False
    assert src.check_open(_client(lambda r: httpx.Response(503)), url) is None
    detail = load("workday_detail_iqvia.json")
    assert src.check_open(_client(lambda r: httpx.Response(200, json=detail)), url) is True


def test_fetch_tenant_paginates_and_raises():
    facets = load("workday_facets_ing.json")
    page = load("workday_jobs_iqvia.json")
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.method == "POST" else {}
        calls.append(body)
        if request.method == "GET":
            return httpx.Response(200, json=load("workday_detail_iqvia.json"))
        if not body["appliedFacets"]:
            return httpx.Response(200, json=facets)
        # only the first page carries the real total, like the live API
        return httpx.Response(200, json={**page, "total": 25 if body["offset"] == 0 else 0})

    src = workday.Workday(tenants=[], delay=0)
    jobs = src.fetch_tenant(_client(handler), "ing.wd3.myworkdayjobs.com", "ICSGBLCOR", "ING")
    pages = [c for c in calls if c.get("appliedFacets")]
    assert [p["offset"] for p in pages] == [0, 20]
    assert len(jobs) == 10
    # the in-scope "N Locations" / no-location postings were resolved through the detail call
    resolved = [j for j in jobs if j.title.startswith("Ethics and Compliance")]
    assert resolved and all(j.location == "Bucuresti, Romania" for j in resolved)

    def failing(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json=facets) if not body["appliedFacets"] else httpx.Response(500)

    with pytest.raises(httpx.HTTPStatusError):
        src.fetch_tenant(_client(failing), "ing.wd3.myworkdayjobs.com", "ICSGBLCOR", "ING")
