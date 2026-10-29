"""CLI: python -m tracker.run <command>

  run [--dry-run]   scrape every source, update data/, send alerts
  ingest-ai         validate data/ai_candidates.json (written by the daily AI sweep) and ingest it
  build-site [dir]  write the dashboard (default _site/)
  test-alert        send the newest alert-worthy job to Telegram, marked as a test
  known-jobs FILE   write "title | company | city" for active jobs (input for the AI sweep)
  chat-ids          list chats that have messaged the bot (to find TELEGRAM_CHAT_ID)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from . import classify, sources
from .notify import Telegram, format_job
from .sources.base import RawJob, generic_check_open, make_client
from .store import Event, SourceResult, Store, now_iso

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("TRACKER_DATA", ROOT / "data"))
AI_CANDIDATES = DATA / "ai_candidates.json"
APPLIED = DATA / "applied.json"  # written by the dashboard's "I have applied" button

MAX_ENRICH_NEW = 40
MAX_ENRICH_BACKLOG = 10
MAX_CHECKS_PER_SOURCE = 30
CHECK_EVERY = timedelta(hours=6)
CHECK_EVERY_GENERIC = timedelta(hours=24)  # ai_sweep listings, checked by plain URL fetch
HEALTH_WARN_AFTER = 3


def log(*args) -> None:
    print(*args, file=sys.stderr, flush=True)


def _fetch(source) -> SourceResult:
    try:
        with make_client() as client:
            jobs = source.fetch(client)
        log(f"[{source.name}] {len(jobs)} listings")
        return SourceResult(source.name, source.complete, jobs)
    except Exception as e:  # one broken source must never stop the others
        log(f"[{source.name}] FAILED: {e!r}")
        traceback.print_exc()
        return SourceResult(source.name, source.complete, [], error=f"{type(e).__name__}: {e}"[:300])


def _due(listing: dict, every: timedelta, now: datetime) -> bool:
    last = listing.get("last_checked") or listing.get("last_seen")
    return not last or now - datetime.fromisoformat(last) >= every


def detail_checks(store: Store, registry: dict, results: list[SourceResult]) -> dict[str, bool]:
    """Ask sources whether listings missing from this run are still open."""
    seen = {f"{r.source}:{r.native_id}" for res in results for r in res.jobs}
    ok_sources = {res.name for res in results if store.source_ok(res)}
    now = datetime.now(timezone.utc)
    verdicts: dict[str, bool] = {}
    with make_client() as client:
        for name in {l["source"] for j in store.jobs.values() for l in j["listings"].values()}:
            src = registry.get(name)
            if src is not None:
                if not hasattr(src, "check_open") or name not in ok_sources:
                    continue
                check, every = src.check_open, CHECK_EVERY
            elif name == "ai_sweep":
                check, every = (lambda c, url: generic_check_open(c, url)), CHECK_EVERY_GENERIC
            else:
                continue
            due = [(k, l) for k, l in store.open_listings(name) if k not in seen and _due(l, every, now)]
            due.sort(key=lambda kl: kl[1].get("last_checked") or "")
            for key, listing in due[:MAX_CHECKS_PER_SOURCE]:
                try:
                    verdict = check(client, listing["url"])
                except Exception as e:
                    log(f"[{name}] check failed for {listing['url']}: {e!r}")
                    verdict = None
                if verdict is not None:
                    verdicts[key] = verdict
    closed = sum(1 for v in verdicts.values() if not v)
    log(f"detail checks: {len(verdicts)} answered, {closed} closed")
    return verdicts


def enrich(store: Store, registry: dict, results: list[SourceResult]) -> None:
    """Fetch descriptions for listings that are about to become NEW jobs, then a small backlog."""
    budget = MAX_ENRICH_NEW
    with make_client() as client:
        for res in results:
            src = registry.get(res.name)
            if not hasattr(src, "enrich"):
                continue
            for raw in res.jobs:
                if budget <= 0:
                    return
                info = store.classify(raw)
                if info is None or raw.description or store.find(raw, info):
                    continue
                try:
                    src.enrich(client, raw)
                except Exception as e:
                    log(f"[{res.name}] enrich failed for {raw.url}: {e!r}")
                budget -= 1


def enrich_backlog(store: Store, registry: dict) -> None:
    todo = [
        j for j in store.jobs.values()
        if j["status"] == "active" and not j["description"] and hasattr(registry.get(j["listings"][j["primary"]]["source"]), "enrich")
    ][:MAX_ENRICH_BACKLOG]
    with make_client() as client:
        for job in todo:
            l = job["listings"][job["primary"]]
            raw = RawJob(l["source"], l["native_id"], l["url"], job["title"], job["company"], job["location"])
            try:
                registry[l["source"]].enrich(client, raw)
            except Exception as e:
                log(f"backlog enrich failed for {l['url']}: {e!r}")
                continue
            if raw.description:
                store.set_description(job["id"], raw.description)


def load_applied() -> dict:
    try:
        return json.loads(APPLIED.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def send_alerts(store: Store, events: list[Event], tg: Telegram, dry_run: bool) -> None:
    applied = load_applied()
    pending = store.meta.setdefault("pending_alerts", [])
    queue = [(e.job_id, e.type, e) for e in events if e.type in ("NEW", "REACTIVATED") and not e.silent]
    queue = [(jid, t, None) for jid, t in pending] + queue
    store.meta["pending_alerts"] = []
    dashboard = os.environ.get("DASHBOARD_URL")
    for jid, etype, event in queue:
        job = store.jobs.get(jid)
        if not job or job["status"] != "active" or jid in applied or not classify.should_alert(job):
            continue
        text, markup = format_job(job, etype, dashboard)
        if dry_run or not tg.configured:
            log(f"--- ALERT ({'dry run' if dry_run else 'telegram not configured'}) ---\n{text}\n{markup}\n")
            continue
        try:
            tg.send(text, markup)
            if event:
                event.notified = True
        except httpx.HTTPError as e:
            log(f"telegram send failed for {jid}: {e!r}")
            store.meta["pending_alerts"].append([jid, etype])


def health_alerts(store: Store, before: dict, tg: Telegram, dry_run: bool) -> None:
    for name, s in store.sources.items():
        prev = before.get(name, {}).get("fail_streak", 0)
        msg = None
        if s["fail_streak"] == HEALTH_WARN_AFTER:
            msg = f"⚠️ Job tracker: source <b>{name}</b> has failed {HEALTH_WARN_AFTER} runs in a row.\n<code>{s.get('last_error')}</code>"
        elif prev >= HEALTH_WARN_AFTER and s["fail_streak"] == 0:
            msg = f"✅ Job tracker: source <b>{name}</b> is working again."
        if msg:
            log(msg)
            if not dry_run:
                try:
                    tg.admin(msg)
                except httpx.HTTPError as e:
                    log(f"admin alert failed: {e!r}")


def cmd_run(args) -> None:
    store = Store(DATA)
    registry = sources.by_name()
    baseline = not store.meta.get("baseline_done")
    if baseline and "linkedin" in registry:
        from .sources import linkedin
        registry["linkedin"].window = linkedin.MONTH

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_fetch, registry.values()))

    enrich(store, registry, results)
    checks = detail_checks(store, registry, results)
    before = json.loads(json.dumps(store.sources))
    events = store.apply(results, checks)
    enrich_backlog(store, registry)
    log("events:", {t: sum(e.type == t for e in events) for t in ("NEW", "UPDATED", "DEACTIVATED", "REACTIVATED")})

    tg = Telegram()
    if baseline:
        if any(store.source_ok(r) for r in results):
            store.meta["baseline_done"] = True
            active = [j for j in store.jobs.values() if j["status"] == "active"]
            msg = (
                f"👋 Job tracker is live. Tracking <b>{len(active)}</b> active compliance roles in Romania "
                f"({sum(classify.should_alert(j) for j in active)} would alert). From now on you'll get a message "
                "for every new or re-posted role in Bucharest, or remote and open to Romania."
            )
            log(msg)
            if not args.dry_run and tg.configured:
                tg.send(msg)
    else:
        send_alerts(store, events, tg, args.dry_run)
    health_alerts(store, before, tg, args.dry_run)

    if not args.dry_run:
        store.save()


def _title_on_page(title: str, page: str) -> bool:
    words = [w for w in re.findall(r"\w{3,}", title.lower())]
    page = page.lower()
    return bool(words) and sum(w in page for w in words) / len(words) >= 0.6


def cmd_ingest_ai(args) -> None:
    if not AI_CANDIDATES.exists():
        log("no AI candidates file")
        return
    candidates = json.loads(AI_CANDIDATES.read_text())
    store = Store(DATA)
    known_urls = {l["url"] for j in store.jobs.values() for l in j["listings"].values()}
    accepted: list[RawJob] = []
    with make_client() as client:
        for c in candidates:
            url, title = c.get("url", ""), c.get("title", "")
            if not url.startswith("http") or not title or url in known_urls:
                continue
            raw = RawJob(
                source="ai_sweep",
                native_id=hashlib.sha1(url.encode()).hexdigest()[:12],
                url=url,
                title=title,
                company=c.get("company", ""),
                location=c.get("location", ""),
                posted=c.get("posted"),
                description=c.get("description", ""),
                remote=bool(c.get("remote")),
            )
            info = store.classify(raw)
            if info is None or store.find(raw, info):
                continue
            try:
                r = client.get(url)
            except httpx.HTTPError as e:
                log(f"[ai] unreachable {url}: {e!r}")
                continue
            if r.status_code != 200 or not _title_on_page(title, r.text) or generic_check_open(client, url) is False:
                log(f"[ai] rejected {url} (status {r.status_code})")
                continue
            accepted.append(raw)
    log(f"[ai] accepted {len(accepted)} of {len(candidates)} candidates")

    events = store.apply([SourceResult("ai_sweep", False, accepted)])
    tg = Telegram()
    if store.meta.get("ai_baseline_done"):
        send_alerts(store, events, tg, args.dry_run)
    else:
        store.meta["ai_baseline_done"] = True
    if not args.dry_run:
        store.save()
        AI_CANDIDATES.unlink()


def cmd_build_site(args) -> None:
    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    store = Store(DATA)
    shutil.copy(ROOT / "site" / "index.html", out / "index.html")
    jobs = list(store.jobs.values())
    (out / "jobs.json").write_text(json.dumps(jobs, ensure_ascii=False))
    events = store.read_events()[-2000:]
    (out / "events.json").write_text(json.dumps(events, ensure_ascii=False))
    status = {"generated": now_iso(), "meta": {k: v for k, v in store.meta.items() if k != "pending_alerts"}, "sources": store.sources}
    (out / "status.json").write_text(json.dumps(status, ensure_ascii=False))
    (out / "applied.json").write_text(json.dumps(load_applied(), ensure_ascii=False))
    log(f"site written to {out} ({len(jobs)} jobs, {len(events)} events)")


def cmd_test_alert(args) -> None:
    store = Store(DATA)
    tg = Telegram()
    candidates = sorted(
        (j for j in store.jobs.values() if j["status"] == "active" and classify.should_alert(j)),
        key=lambda j: (bool(j["description"]), j["first_seen"]),
    )
    if not candidates:
        sys.exit("no alert-worthy active job to send")
    text, markup = format_job(candidates[-1], "NEW", os.environ.get("DASHBOARD_URL"))
    text = "🧪 <i>Test message — this is what an alert looks like.</i>\n\n" + text
    if args.dry_run or not tg.configured:
        print(text, markup)
    else:
        tg.send(text, markup)
        log("sent")


def cmd_known_jobs(args) -> None:
    store = Store(DATA)
    rows = sorted(f"{j['title']} | {j['company']} | {j['city']}" for j in store.jobs.values() if j["status"] == "active")
    Path(args.out).write_text("\n".join(rows) + "\n")
    log(f"{len(rows)} known active jobs written to {args.out}")


def cmd_chat_ids(args) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    r = httpx.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30)
    r.raise_for_status()
    chats = {}
    for u in r.json()["result"]:
        msg = u.get("message") or u.get("my_chat_member") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = " ".join(filter(None, [chat.get("first_name"), chat.get("last_name"), chat.get("title"), f"@{chat['username']}" if chat.get("username") else None]))
    for cid, name in chats.items():
        print(cid, name)
    if not chats:
        print("No chats yet — open the bot in Telegram and press Start, then run this again.")


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m tracker.run")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("run", cmd_run), ("ingest-ai", cmd_ingest_ai), ("test-alert", cmd_test_alert)):
        sp = sub.add_parser(name)
        sp.add_argument("--dry-run", action="store_true")
        sp.set_defaults(fn=fn)
    sp = sub.add_parser("build-site")
    sp.add_argument("out", nargs="?", default=str(ROOT / "_site"))
    sp.set_defaults(fn=cmd_build_site)
    sp = sub.add_parser("known-jobs")
    sp.add_argument("out")
    sp.set_defaults(fn=cmd_known_jobs)
    sub.add_parser("chat-ids").set_defaults(fn=cmd_chat_ids)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
