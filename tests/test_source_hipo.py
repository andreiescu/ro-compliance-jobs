from pathlib import Path

from tracker.sources import hipo

FIX = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIX / name).read_text()


def test_parse_search():
    jobs = {j.native_id: j for j in hipo.parse_search(_read("hipo_search.html"))}
    assert set(jobs) == {"273155", "265034", "267037", "272465"}
    job = jobs["273155"]
    assert job.title == "AML/KYC Compliance Officer"
    assert job.company == "Grant Thornton Romania"
    assert job.location == "BUCURESTI, Romania"
    assert job.url.startswith("https://www.hipo.ro/locuri-de-munca/locuri_de_munca/273155/")
    assert job.posted is None
    assert job.extra["refreshed"] == "2026-10-29"
    assert jobs["267037"].location == "Hybrid, Romania"


def test_parse_search_splits_reposted_employer():
    job = {j.native_id: j for j in hipo.parse_search(_read("hipo_search.html"))}["265034"]
    assert job.company == "Marquardt"
    assert job.title == "Legal Counsel - Compliance Coordinator (F/M)"
    assert job.location == "Sibiu, Romania"


def test_has_next_page():
    assert hipo.has_next_page(_read("hipo_search.html"))
    assert not hipo.has_next_page("<html><body><div class='pager'></div></body></html>")


def test_parse_posting_form_layout():
    info = hipo.parse_posting(_read("hipo_job_form.html"))
    assert not info["closed"]
    assert "Law no. 129/2019" in info["description"]


def test_parse_posting_freeform_layout():
    info = hipo.parse_posting(_read("hipo_job_freeform.html"))
    assert not info["closed"]
    assert "Data Protection Office" in info["description"]
    assert "margin-bottom" not in info["description"]


def test_parse_posting_closed():
    assert hipo.parse_posting(_read("hipo_job_closed.html"))["closed"]
