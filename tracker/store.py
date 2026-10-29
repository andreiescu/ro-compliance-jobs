"""Job state on disk and the lifecycle rules: NEW, UPDATED, DEACTIVATED, REACTIVATED.

A *job* is one role at one employer. It can be seen through several *listings*
(the same role on LinkedIn and on the company's own careers site); listings are
keyed ``source:native_id`` and grouped into a job by ``classify.fingerprint``.
A job is active while at least one of its listings is open.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import classify
from .sources.base import RawJob

MISSES_TO_CLOSE = 3  # consecutive successful runs a listing must be absent from a complete source
SOURCE_DROP_RATIO = 0.3  # fewer results than this share of last run's count = treat run as failed


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class SourceResult:
    name: str
    complete: bool
    jobs: list[RawJob] = field(default_factory=list)
    error: str | None = None


@dataclass
class Event:
    ts: str
    job_id: str
    type: str  # NEW | UPDATED | DEACTIVATED | REACTIVATED
    diff: dict = field(default_factory=dict)
    notified: bool = False
    silent: bool = False  # from a source's first successful run: existing jobs, not news

    def to_json(self) -> dict:
        return self.__dict__.copy()


def content_hash(title: str, location: str, description: str) -> str:
    norm = " ".join(f"{title}\n{location}\n{description}".split()).lower()
    return hashlib.sha1(norm.encode()).hexdigest()[:16]


class Store:
    def __init__(self, data_dir: Path):
        self.dir = data_dir
        self.jobs_path = data_dir / "jobs.json"
        self.events_path = data_dir / "events.jsonl"
        if self.jobs_path.exists():
            state = json.loads(self.jobs_path.read_text())
        else:
            state = {}
        self.meta: dict = state.get("meta", {"baseline_done": False, "runs": 0})
        self.sources: dict = state.get("sources", {})
        self.jobs: dict[str, dict] = state.get("jobs", {})
        self.new_events: list[Event] = []
        self._reindex()

    def _reindex(self) -> None:
        self.by_listing = {k: jid for jid, j in self.jobs.items() for k in j["listings"]}
        self.by_fp = {j["fingerprint"]: jid for jid, j in self.jobs.items()}
        self.by_title_city: dict[str, list[str]] = {}
        for jid, j in self.jobs.items():
            self.by_title_city.setdefault(classify.title_city_key(j["title"], j["city"]), []).append(jid)

    # ---- lookups -----------------------------------------------------------

    def classify(self, raw: RawJob) -> dict | None:
        """Classification for a raw listing, or None when it's out of scope."""
        cat = classify.category(raw.title)
        if cat is None:
            return None
        where = classify.place(raw.location, raw.remote)
        if not classify.in_scope(where):
            return None
        return {
            "category": cat,
            "is_core": cat != "Adjacent",
            **where,
            "fingerprint": classify.fingerprint(raw.company, raw.title, where["city"]),
        }

    def find(self, raw: RawJob, info: dict | None = None) -> str | None:
        key = f"{raw.source}:{raw.native_id}"
        if key in self.by_listing:
            return self.by_listing[key]
        info = info or self.classify(raw)
        if not info:
            return None
        if info["fingerprint"] in self.by_fp:
            return self.by_fp[info["fingerprint"]]
        # A job board reposting an employer's role: match on title + city, as long as
        # one side of the match is the job board.
        raw_is_agg = classify.is_aggregator(raw.company)
        for jid in self.by_title_city.get(classify.title_city_key(raw.title, info["city"]), []):
            if raw_is_agg or classify.is_aggregator(self.jobs[jid]["company"]):
                return jid
        return None

    def open_listings(self, source: str) -> list[tuple[str, dict]]:
        return [
            (k, l)
            for j in self.jobs.values()
            for k, l in j["listings"].items()
            if l["source"] == source and l["status"] == "open"
        ]

    # ---- the run -----------------------------------------------------------

    def source_ok(self, res: SourceResult) -> bool:
        """A source run counts only if it didn't error and didn't suspiciously collapse."""
        if res.error:
            return False
        prev = self.sources.get(res.name, {}).get("last_count", 0)
        return not (prev >= 5 and len(res.jobs) < prev * SOURCE_DROP_RATIO)

    def apply(self, results: list[SourceResult], checks: dict[str, bool] | None = None) -> list[Event]:
        """Fold one run's results into the state and return the events it produced.

        ``checks`` maps listing keys to a detail-check verdict (True open, False closed)
        for listings that weren't in this run's results.
        """
        ts = now_iso()
        checks = checks or {}
        seen: set[str] = set()
        events: list[Event] = []
        touched: set[str] = set()

        for res in results:
            ok = self.source_ok(res)
            first_run = not self.sources.get(res.name, {}).get("last_ok")
            self._record_source_health(res, ok, ts)
            if not ok:
                continue
            for raw in res.jobs:
                info = self.classify(raw)
                if info is None:
                    continue
                key = f"{raw.source}:{raw.native_id}"
                if key in seen:
                    continue
                seen.add(key)
                jid = self.find(raw, info)
                if jid is None:
                    jid = self._create(raw, info, key, ts)
                    events.append(Event(ts, jid, "NEW", silent=first_run))
                    touched.add(jid)
                    continue
                job = self.jobs[jid]
                listing = job["listings"].get(key)
                if listing is None:
                    listing = job["listings"][key] = self._listing(raw, ts)
                    self.by_listing[key] = jid
                listing.update(status="open", last_seen=ts, misses=0, url=raw.url)
                if classify.is_aggregator(job["company"]) and not classify.is_aggregator(raw.company):
                    # Prefer the employer's own listing over a job board's repost.
                    # Adopting its fields silently: this is a better source, not a change to the role.
                    job.update(company=raw.company, fingerprint=info["fingerprint"], primary=key)
                    self.by_fp[info["fingerprint"]] = jid
                    self._update_fields(job, raw, info)
                job["last_seen"] = ts
                if job["status"] == "inactive" and jid not in touched:
                    job["status"] = "active"
                    job["last_changed"] = ts
                    events.append(Event(ts, jid, "REACTIVATED", {"via": raw.source}))
                    touched.add(jid)
                if key == job["primary"]:
                    diff = self._update_fields(job, raw, info)
                    if diff:
                        job["last_changed"] = ts
                        events.append(Event(ts, jid, "UPDATED", diff))

            if res.complete:
                for key, listing in self.open_listings(res.name):
                    if key in seen:
                        continue
                    listing["misses"] = listing.get("misses", 0) + 1
                    if listing["misses"] >= MISSES_TO_CLOSE:
                        listing["status"] = "closed"
                        listing["closed_at"] = ts

        for key, is_open in checks.items():
            jid = self.by_listing.get(key)
            if jid is None or key in seen:
                continue
            listing = self.jobs[jid]["listings"][key]
            listing["last_checked"] = ts
            if not is_open:
                listing["status"] = "closed"
                listing["closed_at"] = ts

        for jid, job in self.jobs.items():
            if job["status"] == "active" and all(l["status"] == "closed" for l in job["listings"].values()):
                job["status"] = "inactive"
                job["last_changed"] = ts
                events.append(Event(ts, jid, "DEACTIVATED"))

        self.meta["runs"] = self.meta.get("runs", 0) + 1
        self.meta["last_run"] = ts
        self.new_events.extend(events)
        return events

    def set_description(self, jid: str, description: str) -> None:
        job = self.jobs[jid]
        job["description"] = description
        job["content_hash"] = content_hash(job["title"], job["location"], description)

    # ---- internals ---------------------------------------------------------

    @staticmethod
    def _listing(raw: RawJob, ts: str) -> dict:
        return {
            "source": raw.source,
            "native_id": raw.native_id,
            "url": raw.url,
            "status": "open",
            "first_seen": ts,
            "last_seen": ts,
            "misses": 0,
        }

    def _create(self, raw: RawJob, info: dict, key: str, ts: str) -> str:
        jid = hashlib.sha1(key.encode()).hexdigest()[:12]
        self.jobs[jid] = {
            "id": jid,
            "fingerprint": info["fingerprint"],
            "title": raw.title,
            "company": raw.company,
            "location": raw.location,
            "city": info["city"],
            "remote": info["remote"],
            "remote_ro": info["remote_ro"],
            "category": info["category"],
            "is_core": info["is_core"],
            "posted": raw.posted,
            "description": raw.description,
            "extra": raw.extra,
            "status": "active",
            "first_seen": ts,
            "last_seen": ts,
            "last_changed": ts,
            "primary": key,
            "content_hash": content_hash(raw.title, raw.location, raw.description),
            "listings": {key: self._listing(raw, ts)},
        }
        self.by_listing[key] = jid
        self.by_fp[info["fingerprint"]] = jid
        self.by_title_city.setdefault(classify.title_city_key(raw.title, info["city"]), []).append(jid)
        return jid

    def _update_fields(self, job: dict, raw: RawJob, info: dict) -> dict:
        # List pages usually carry no description; keep the stored one rather than
        # reporting it as "changed" to empty.
        description = raw.description or job["description"]
        new_hash = content_hash(raw.title, raw.location, description)
        diff = {}
        if new_hash != job["content_hash"]:
            for field_name, new in (("title", raw.title), ("location", raw.location)):
                if job[field_name] != new:
                    diff[field_name] = [job[field_name], new]
            if description != job["description"]:
                diff["description"] = "changed"
            job.update(
                title=raw.title,
                location=raw.location,
                description=description,
                content_hash=new_hash,
                city=info["city"],
                remote=info["remote"],
                remote_ro=info["remote_ro"],
                category=info["category"],
                is_core=info["is_core"],
            )
        # Posting dates drift (relative "N days ago", daily refreshes), so they're kept
        # current but never reported as an update on their own.
        if raw.posted:
            job["posted"] = raw.posted
        return diff

    def _record_source_health(self, res: SourceResult, ok: bool, ts: str) -> None:
        s = self.sources.setdefault(res.name, {"fail_streak": 0})
        s["last_attempt"] = ts
        if ok:
            s.update(last_ok=ts, last_count=len(res.jobs), fail_streak=0, last_error=None)
        else:
            s["fail_streak"] = s.get("fail_streak", 0) + 1
            s["last_error"] = res.error or f"only {len(res.jobs)} results (was {s.get('last_count')})"

    # ---- persistence -------------------------------------------------------

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        state = {"meta": self.meta, "sources": self.sources, "jobs": self.jobs}
        self.jobs_path.write_text(json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        if self.new_events:
            with self.events_path.open("a") as f:
                for e in self.new_events:
                    f.write(json.dumps(e.to_json(), ensure_ascii=False) + "\n")
            self.new_events = []

    def read_events(self) -> list[dict]:
        if not self.events_path.exists():
            return []
        return [json.loads(line) for line in self.events_path.read_text().splitlines() if line.strip()]
