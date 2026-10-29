import json
from datetime import date
from pathlib import Path

from tracker.sources import juridice

FIX = Path(__file__).parent / "fixtures"


def test_parse_posts():
    jobs = {j.native_id: j for j in juridice.parse_posts(json.loads((FIX / "juridice_posts.json").read_text()))}
    assert len(jobs) == 3
    job = jobs["81413"]
    assert job.title == "Legal & Compliance Officer"
    assert job.company == "NETOPIA PAYMENTS"
    assert job.url == "https://cariere.juridice.ro/2026/09/netopia-payments-is-looking-for-a-legal-compliance-officer.html"
    assert job.posted == "2026-09-18"
    assert job.location.endswith("Romania")
    assert job.description
    assert jobs["81686"].title == "Junior Lawyer | Tech & Data Privacy"
    assert jobs["81746"].company == "FRANDEȘ LAW"


def test_split_title_without_hiring_phrase():
    assert juridice.split_title("Legal Talent Search | Lawyer") == ("Legal Talent Search | Lawyer", "")
    assert juridice.split_title("ADM is looking for a Junior Legal Counsel") == ("Junior Legal Counsel", "ADM")
    assert juridice.split_title("FRANDEȘ LAW recrutează avocat definitiv") == ("avocat definitiv", "FRANDEȘ LAW")


def test_post_month():
    assert juridice.post_month("https://cariere.juridice.ro/2026/09/x.html") == date(2026, 9, 1)
    assert juridice.post_month("https://example.com/x") is None
