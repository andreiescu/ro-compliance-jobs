from tracker.sources.base import RawJob
from tracker.store import MISSES_TO_CLOSE, SourceResult, Store


def job(native_id="1", title="Compliance Officer", company="Acme", location="Bucharest, Romania", source="ats", **kw):
    return RawJob(source=source, native_id=native_id, url=f"https://x/{source}/{native_id}", title=title,
                  company=company, location=location, **kw)


def run(store, *results, checks=None):
    return [(e.type, store.jobs[e.job_id]["title"]) for e in store.apply(list(results), checks)]


def res(name, jobs, complete=True, error=None):
    return SourceResult(name, complete, jobs, error)


def test_new_then_unchanged(tmp_path):
    s = Store(tmp_path)
    assert run(s, res("ats", [job()])) == [("NEW", "Compliance Officer")]
    assert run(s, res("ats", [job()])) == []


def test_out_of_scope_jobs_are_ignored(tmp_path):
    s = Store(tmp_path)
    assert run(s, res("ats", [job(title="Java Developer"), job("2", location="Berlin, Germany")])) == []
    assert s.jobs == {}


def test_updated_on_title_or_description_change(tmp_path):
    s = Store(tmp_path)
    run(s, res("ats", [job(description="old")]))
    events = s.apply([res("ats", [job(title="Senior Compliance Officer", description="new")])])
    assert [e.type for e in events] == ["UPDATED"]
    assert events[0].diff["title"] == ["Compliance Officer", "Senior Compliance Officer"]
    assert events[0].diff["description"] == "changed"


def test_list_without_description_does_not_count_as_update(tmp_path):
    s = Store(tmp_path)
    run(s, res("ats", [job(description="full text")]))
    assert run(s, res("ats", [job(description="")])) == []


def test_deactivated_after_misses_on_complete_source(tmp_path):
    s = Store(tmp_path)
    run(s, res("ats", [job(), job("2", title="AML Analyst")]))
    for _ in range(MISSES_TO_CLOSE - 1):
        assert run(s, res("ats", [job("2", title="AML Analyst")])) == []
    assert run(s, res("ats", [job("2", title="AML Analyst")])) == [("DEACTIVATED", "Compliance Officer")]


def test_failed_source_never_counts_misses(tmp_path):
    s = Store(tmp_path)
    run(s, res("ats", [job()]))
    for _ in range(MISSES_TO_CLOSE + 2):
        assert run(s, res("ats", [], error="HTTP 503")) == []
    assert s.sources["ats"]["fail_streak"] == MISSES_TO_CLOSE + 2
    assert next(iter(s.jobs.values()))["status"] == "active"


def test_collapsed_result_count_is_treated_as_failure(tmp_path):
    s = Store(tmp_path)
    many = [job(str(i), title=f"Compliance Officer {i}") for i in range(10)]
    run(s, res("ats", many))
    for _ in range(MISSES_TO_CLOSE):
        assert run(s, res("ats", many[:1])) == []
    assert all(j["status"] == "active" for j in s.jobs.values())


def test_incomplete_source_closes_only_via_detail_check(tmp_path):
    s = Store(tmp_path)
    run(s, res("linkedin", [job(source="linkedin")], complete=False))
    for _ in range(MISSES_TO_CLOSE + 1):
        assert run(s, res("linkedin", [], complete=False)) == []
    key = "linkedin:1"
    assert run(s, res("linkedin", [], complete=False), checks={key: True}) == []
    assert run(s, res("linkedin", [], complete=False), checks={key: False}) == [("DEACTIVATED", "Compliance Officer")]


def test_reactivated_when_seen_again(tmp_path):
    s = Store(tmp_path)
    run(s, res("linkedin", [job(source="linkedin")], complete=False))
    run(s, res("linkedin", [], complete=False), checks={"linkedin:1": False})
    assert run(s, res("linkedin", [job(source="linkedin")], complete=False)) == [("REACTIVATED", "Compliance Officer")]


def test_same_role_on_two_sources_is_one_job(tmp_path):
    s = Store(tmp_path)
    events = run(
        s,
        res("linkedin", [job(source="linkedin", company="Acme Romania", location="Bucharest, Bucharest, Romania")], complete=False),
        res("ats", [job(company="Acme SRL", title="Compliance Officer (m/f/d)", location="București")]),
    )
    assert events == [("NEW", "Compliance Officer")]
    (only,) = s.jobs.values()
    assert set(only["listings"]) == {"linkedin:1", "ats:1"}


def test_job_stays_active_while_any_listing_is_open(tmp_path):
    s = Store(tmp_path)
    run(s, res("linkedin", [job(source="linkedin")], complete=False), res("ats", [job()]))
    run(s, res("linkedin", [], complete=False), res("ats", [job()]), checks={"linkedin:1": False})
    assert next(iter(s.jobs.values()))["status"] == "active"


def test_aggregator_repost_merges_into_employer_job(tmp_path):
    s = Store(tmp_path)
    run(s, res("linkedin", [job("9", source="linkedin", company="Hipo.ro", title="KYC Analyst", location="Ilfov, Romania")], complete=False))
    events = run(s, res("ats", [job(title="KYC Analyst", company="Ayvens", location="Bucharest, Romania")]))
    assert events == []
    (only,) = s.jobs.values()
    assert only["company"] == "Ayvens" and only["primary"] == "ats:1"


def test_save_and_reload_round_trip(tmp_path):
    s = Store(tmp_path)
    run(s, res("ats", [job()]))
    s.save()
    again = Store(tmp_path)
    assert again.jobs == s.jobs
    assert [e["type"] for e in again.read_events()] == ["NEW"]
    assert run(again, res("ats", [job()])) == []


def test_first_run_of_a_source_is_silent(tmp_path):
    s = Store(tmp_path)
    first = s.apply([res("ats", [job()])])
    assert [(e.type, e.silent) for e in first] == [("NEW", True)]
    later = s.apply([res("ats", [job(), job("2", title="AML Analyst")]), res("hipo", [job("7", source="hipo", title="KYC Analyst")])])
    assert sorted((e.type, s.jobs[e.job_id]["title"], e.silent) for e in later) == [
        ("NEW", "AML Analyst", False),
        ("NEW", "KYC Analyst", True),
    ]
