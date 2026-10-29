import json
from datetime import date
from pathlib import Path

from tracker.sources import posturi_gov

FIX = Path(__file__).parent / "fixtures"


def test_parse_jobs_skips_past_deadlines_and_reads_terms():
    data = json.loads((FIX / "posturi_gov_jobs.json").read_text())
    jobs = {j.native_id: j for j in posturi_gov.parse_jobs(data, today=date(2026, 10, 29))}
    assert set(jobs) == {"45210", "45133"}  # 45505's deadline was set in the past
    job = jobs["45133"]
    assert job.title == "AUDITOR PUBLIC INTERN IA"
    assert job.url.startswith("https://posturi.gov.ro/joburi/")
    assert job.location == "BUCUREŞTI, București, Romania"
    assert job.posted == "2026-10-26"
    assert job.extra["deadline"] == "2026-11-23"
    assert job.extra["kind"] == "Funcție contractuală"
    assert job.description
    assert jobs["45210"].location.startswith("TIMIŞOARA")


def test_parse_page():
    info = posturi_gov.parse_page((FIX / "posturi_gov_page.html").read_text())
    assert info == {"company": "UAT Comuna Bocșa", "valid_through": "2026-11-02"}
    assert posturi_gov.parse_page("<html></html>") == {"company": "", "valid_through": None}
