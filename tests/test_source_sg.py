import json
from pathlib import Path

from tracker.sources import sg

FIX = Path(__file__).parent / "fixtures"


def test_parse_settings():
    s = sg.parse_settings((FIX / "sg_search_page.html").read_text())
    assert s["csrf"]
    assert s["base_url"].startswith("https://api.socgen.com/")
    assert len(s["romania_ids"]) == 4


def test_parse_results_dedupes_languages_and_fills_fields():
    jobs = sg.parse_results(json.loads((FIX / "sg_search.json").read_text()))
    by_id = {j.native_id: j for j in jobs}
    assert len(jobs) == 5  # 26000D5O is indexed in en + fr
    assert "/en/" in by_id["26000D5O"].url
    job = by_id["26000HW1"]
    assert job.title == "Compliance Level 2 Controller"
    assert job.url == "https://careers.societegenerale.com/en/job-offers/compliance-level-2-controller-26000HW1-en"
    assert job.location == "Bucuresti, Romania"
    assert job.posted == "2026-09-14"
    assert job.company == "Société Générale European Business Services"
    assert by_id["26000KQE"].company == "BRD - Groupe Société Générale"
    assert "control testing" in job.description
    assert by_id["26000MUT"].location == "Romania"


def test_query_body_filters_romania():
    body = sg.query_body(100)
    assert {"type": "multi", "name": "sourcecsv1", "op": "eq", "values": ["ROU"]} in body["query"]["advanced"]
    assert body["query"]["skipFrom"] == 100
