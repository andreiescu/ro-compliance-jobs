"""Telegram alerts."""
from __future__ import annotations

import html
import os

import httpx

TELEGRAM_LIMIT = 4096
SOURCE_NAMES = {
    "linkedin": "LinkedIn",
    "workday": "Company careers (Workday)",
    "ats": "Company careers",
    "sg": "Société Générale careers",
    "hipo": "Hipo.ro",
    "jooble": "Jooble",
    "juridice": "cariere.juridice.ro",
    "posturi_gov": "posturi.gov.ro",
    "banks": "Bank careers",
    "ai_sweep": "AI sweep",
}
HEADERS = {
    "NEW": "🆕 <b>New compliance role</b>",
    "REACTIVATED": "🔁 <b>Listing is back</b>",
}


def _esc(s: str | None) -> str:
    return html.escape(s or "", quote=False)


def format_job(job: dict, event_type: str, dashboard_url: str | None = None) -> tuple[str, dict]:
    """Build the HTML message text and inline keyboard for one job."""
    header = HEADERS.get(event_type, f"<b>{_esc(event_type.title())}</b>")
    if job.get("remote_ro") and job["city"] not in ("Bucharest",):
        header += " · 🌍 remote, open to Romania"
    where = job["city"] if job["city"] == job["location"] else f"{job['city']} ({job['location']})"
    sources = sorted({SOURCE_NAMES.get(l["source"], l["source"]) for l in job["listings"].values()})
    lines = [
        header,
        "",
        f"<b>{_esc(job['title'])}</b>",
        f"🏢 {_esc(job['company'])}",
        f"📍 {_esc(where)}{' · remote' if job.get('remote') else ''}",
        f"🗂 {_esc(job['category'])}",
    ]
    if job.get("posted"):
        lines.append(f"📅 Posted {_esc(job['posted'])}")
    lines.append(f"🔎 Found on {_esc(', '.join(sources))}")
    head = "\n".join(lines)

    description = (job.get("description") or "").strip()
    if description:
        room = TELEGRAM_LIMIT - len(head) - 200
        excerpt = description if len(description) <= min(room, 2500) else description[: min(room, 2500)].rsplit(" ", 1)[0] + " …"
        text = f"{head}\n\n<blockquote expandable>{_esc(excerpt)}</blockquote>"
    else:
        text = f"{head}\n\n<i>No description captured — open the posting for details.</i>"

    url = job["listings"][job["primary"]]["url"]
    buttons = [[{"text": "Open job posting ↗", "url": url}]]
    if dashboard_url:
        buttons.append([{"text": "Dashboard", "url": f"{dashboard_url.rstrip('/')}/#job-{job['id']}"}])
    return text, {"inline_keyboard": buttons}


class Telegram:
    def __init__(self, token: str | None = None, chat_id: str | None = None, admin_chat_id: str | None = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.admin_chat_id = admin_chat_id or os.environ.get("TELEGRAM_ADMIN_CHAT_ID")

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, reply_markup: dict | None = None, chat_id: str | None = None) -> None:
        payload = {
            "chat_id": chat_id or self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        r = httpx.post(f"https://api.telegram.org/bot{self.token}/sendMessage", json=payload, timeout=30)
        r.raise_for_status()

    def admin(self, text: str) -> None:
        if self.token and self.admin_chat_id:
            self.send(text, chat_id=self.admin_chat_id)
