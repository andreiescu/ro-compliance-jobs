# ro-compliance-jobs

Every hour, this repo checks job boards and employers' careers sites for compliance roles in Romania. It covers compliance officer and specialist roles, AML/KYC/financial crime, and data protection/DPO. It records how each listing changes over time and sends a Telegram alert when a relevant role appears.

- **Dashboard:** https://andreiescu.github.io/ro-compliance-jobs/
- **Alerts:** the bot posts to the Telegram group for core roles in Bucharest, or remote and open to Romania. It alerts when a listing is new, or when it comes back after disappearing.

## How it works

`.github/workflows/track.yml` runs every hour at :17.

1. **Scrape.** Each module in `tracker/sources/` fetches its listings:
   - LinkedIn (guest search)
   - Workday career sites
   - Greenhouse, Lever, Ashby, SmartRecruiters, Workable and Teamtailor boards
   - Société Générale careers
   - Hipo.ro
   - posturi.gov.ro
   - Romanian bank and insurer careers pages
2. **Classify.** `tracker/classify.py` drops anything that isn't a compliance role in Romania, or remote and open to Romania. It marks each kept role as *core* or *adjacent*; adjacent roles (IT GRC, tax, trade, regulatory affairs…) show on the dashboard but never alert.
3. **Update state.** `tracker/store.py` folds the results into `data/jobs.json` and appends changes to `data/events.jsonl`. A change is one of:
   - `NEW`
   - `UPDATED`: title, location or description changed
   - `DEACTIVATED`: every listing of the job is closed
   - `REACTIVATED`
4. **Alert.** New and reactivated core roles in Bucharest, or remote and open to Romania, go to Telegram (`tracker/notify.py`).
5. **Publish.** The dashboard (`site/index.html` plus the JSON data) is deployed to GitHub Pages.

### Rules for when a listing counts as closed

- **Full-list sources.** Sources marked `complete` return their whole current list. A listing missing from 3 successful runs in a row is closed.
- **Windowed sources.** LinkedIn search, Hipo and similar sources only cover a window of results, so absence proves nothing. These are checked by opening the listing itself.
- **Failed runs.** A source that errors, or suddenly returns far fewer results than last time, is treated as failed. It never closes anything. If it fails 3 runs in a row, the admin chat gets a warning.
- **First run of a source.** A source's first successful run is a silent baseline, so adding a new source doesn't flood the chat.

### Daily AI sweep

`.github/workflows/ai-sweep.yml` runs Claude once a day to look for roles the scrapers miss: eJobs, agencies, banks that block scraping, and so on. Its candidates go through `tracker.run ingest-ai`, which only accepts URLs that load and actually show the job title. Accepted candidates then go through the same pipeline as everything else.

## Running locally

```sh
uv sync
uv run pytest
uv run python -m tracker.run run --dry-run        # scrape and print alerts, change nothing
TRACKER_DATA=/tmp/jobs uv run python -m tracker.run run   # real run into a scratch dir
uv run python -m tracker.run build-site /tmp/site # build the dashboard
```

Other commands:
- `test-alert` sends a sample alert.
- `chat-ids` lists the chats the bot can see, to find a chat ID.

## Configuration

Repository secrets:
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`: where alerts go.
- `TELEGRAM_ADMIN_CHAT_ID`: optional; where source-health warnings go.
- `CLAUDE_CODE_OAUTH_TOKEN`: for the AI sweep; create it with `claude setup-token`.

Repository variable:
- `DASHBOARD_URL`

## Adding a source

Add a module to `tracker/sources/` that exposes `SOURCE`, following the contract in `tracker/sources/base.py`. List it in `tracker/sources/__init__.py` and add fixture-based tests. Set `complete = True` only if `fetch` returns the full list or raises.
