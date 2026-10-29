import json

from tracker import run
from tracker.sources.base import RawJob
from tracker.store import SourceResult, Store


class FakeTelegram:
    configured = True

    def __init__(self):
        self.sent = []

    def send(self, text, markup=None):
        self.sent.append(text)


def test_no_alert_for_jobs_already_applied_to(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.apply([SourceResult("ats", True, [])])  # baseline: ats has run before, so its next jobs alert
    raws = [
        RawJob("ats", str(i), f"https://x/{i}", f"Compliance Officer {i}", "Acme", "Bucharest, Romania")
        for i in (1, 2)
    ]
    events = store.apply([SourceResult("ats", True, raws)])
    applied_id = events[0].job_id
    applied = tmp_path / "applied.json"
    applied.write_text(json.dumps({applied_id: {"applied_at": "2026-10-29T10:00:00Z"}}))
    monkeypatch.setattr(run, "APPLIED", applied)

    tg = FakeTelegram()
    run.send_alerts(store, events, tg, dry_run=False)
    assert len(tg.sent) == 1 and "Compliance Officer 2" in tg.sent[0]
