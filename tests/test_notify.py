from tracker.notify import TELEGRAM_LIMIT, format_job


def make_job(**kw):
    job = {
        "id": "abc123",
        "title": "AML Officer <French>",
        "company": "Bank & Co",
        "location": "Bucharest, Romania",
        "city": "Bucharest",
        "remote": False,
        "remote_ro": False,
        "category": "AML / KYC / FinCrime",
        "posted": "2026-10-29",
        "description": "Do compliance things. " * 50,
        "primary": "linkedin:1",
        "listings": {
            "linkedin:1": {"source": "linkedin", "url": "https://www.linkedin.com/jobs/view/1"},
            "ats:1": {"source": "ats", "url": "https://jobs.lever.co/x/1"},
        },
    }
    job.update(kw)
    return job


def test_message_escapes_html_and_links_to_primary_listing():
    text, markup = format_job(make_job(), "NEW", "https://me.github.io/ro-compliance-jobs/")
    assert "AML Officer &lt;French&gt;" in text and "Bank &amp; Co" in text
    assert "🆕" in text and "LinkedIn" in text and "Company careers" in text
    assert markup["inline_keyboard"][0][0] == {"text": "Open job posting ↗", "url": "https://www.linkedin.com/jobs/view/1"}
    assert markup["inline_keyboard"][1][0]["url"] == "https://me.github.io/ro-compliance-jobs/#job-abc123"


def test_long_description_fits_telegram_limit():
    text, _ = format_job(make_job(description="word " * 5000), "REACTIVATED")
    assert len(text) < TELEGRAM_LIMIT
    assert "🔁" in text and text.rstrip().endswith("</blockquote>")


def test_no_description_says_so():
    text, markup = format_job(make_job(description=""), "NEW")
    assert "No description captured" in text
    assert len(markup["inline_keyboard"]) == 1


def test_remote_role_is_flagged():
    text, _ = format_job(make_job(city="Remote - EMEA", location="Remote - EMEA", remote=True, remote_ro=True), "NEW")
    assert "remote, open to Romania" in text
