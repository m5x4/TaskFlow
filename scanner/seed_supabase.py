#!/usr/bin/env python3
"""Populate Supabase with demo tasks, without Gmail or a database password.

Writes through the PostgREST API using the secret key already in
web/.env.local, so it works even while DATABASE_URL is unset or wrong. The
schema must exist first (migrations/001_init.sql, applied via `taskflow
init-db` or pasted into the Supabase SQL editor).

    python3 seed_supabase.py           # insert / refresh the demo rows
    python3 seed_supabase.py --clear   # delete them again

Every row it writes is tagged: emails have gmail_message_id starting with
`seed-`, so --clear can find them and real scanned mail is never touched.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / "web" / ".env.local"
SEED_PREFIX = "seed-"

_NON_ALNUM = re.compile(r"[^a-z0-9 ]")
_WHITESPACE = re.compile(r"\s+")


def fingerprint(title: str, thread_id: str) -> str:
    """Mirrors taskflow.models.fingerprint so seeded rows dedupe the same way."""
    normalized = _NON_ALNUM.sub("", title.lower())
    normalized = _WHITESPACE.sub(" ", normalized).strip()
    return hashlib.sha256(f"{thread_id}::{normalized}".encode("utf-8")).hexdigest()


def load_env() -> tuple[str, str]:
    if not ENV_FILE.exists():
        sys.exit(f"missing {ENV_FILE} — copy web/.env.local.example first")
    values: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    url = values.get("SUPABASE_URL", "").rstrip("/")
    key = values.get("SUPABASE_SECRET_KEY", "")
    if not url or not key:
        sys.exit("SUPABASE_URL and SUPABASE_SECRET_KEY must be set in web/.env.local")
    return url, key


def request(method: str, url: str, key: str, body=None, prefer: str | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("apikey", key)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    if prefer:
        req.add_header("Prefer", prefer)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()
        if "PGRST205" in detail:
            sys.exit(
                "the tables do not exist yet — apply migrations/001_init.sql "
                "(Supabase SQL editor, or `python -m taskflow init-db`) and re-run"
            )
        sys.exit(f"{method} {url} -> {exc.code}: {detail}")
    return json.loads(raw) if raw else []


# --- the demo data ----------------------------------------------------------
# Shaped like real extraction output: a spread across all four buckets, two
# tasks sharing one thread, and one already in progress.

NOW = datetime.now(timezone.utc)


def ago(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).isoformat()


EMAILS = [
    {
        "key": "checkout",
        "sender": "Dana Okafor <dana@brightpathclinic.example>",
        "subject": "Checkout is broken on mobile",
        "received_at": ago(3),
        "snippet": "Two patients told me the Book Now button does nothing on their phones.",
    },
    {
        "key": "hours",
        "sender": "Marcus Reid <marcus@brightpathclinic.example>",
        "subject": "New opening hours + a typo",
        "received_at": ago(20),
        "snippet": "We're open until 7pm on Thursdays now. Also 'recieve' on the contact page.",
    },
    {
        "key": "seo",
        "sender": "Priya Nair <priya@northlinemarketing.example>",
        "subject": "Site speed is hurting your search ranking",
        "received_at": ago(52),
        "snippet": "Largest Contentful Paint is 4.8s on mobile. The hero image is 3.2MB.",
    },
    {
        "key": "testimonials",
        "sender": "Dana Okafor <dana@brightpathclinic.example>",
        "subject": "Can we add a testimonials section?",
        "received_at": ago(96),
        "snippet": "Nothing urgent — a few quotes from patients on the homepage would be nice.",
    },
    {
        "key": "ssl",
        "sender": "certs@registrar.example",
        "subject": "Certificate expires in 14 days",
        "received_at": ago(30),
        "snippet": "The certificate for brightpathclinic.example expires on the 16th.",
    },
]

TASKS = [
    {
        "email": "checkout",
        "title": "Fix the Book Now button on mobile Safari",
        "description": (
            "Patients on iPhones report the appointment booking button does "
            "nothing. Desktop is unaffected, so this is likely a touch-event or "
            "overlay z-index problem in the booking widget."
        ),
        "urgency": "critical",
        "impact": "high",
        "category": "bug",
        "confidence": 0.93,
        "status": "open",
        "due_in_days": 1,
        "files": [
            ("components/BookingWidget.tsx", "Owns the button and its click handler", 0.72),
            ("app/globals.css", "Overlay stacking could be swallowing the tap", 0.44),
        ],
    },
    {
        "email": "ssl",
        "title": "Renew the TLS certificate before the 16th",
        "description": (
            "Registrar notice: the certificate for the production domain expires "
            "in 14 days. Renewal is a config change, not a code change."
        ),
        "urgency": "high",
        "impact": "high",
        "category": "infra",
        "confidence": 0.88,
        "status": "in_progress",
        "due_in_days": 12,
        "files": [],
    },
    {
        "email": "hours",
        "title": "Update Thursday opening hours to 7pm",
        "description": "Thursday now closes at 7pm rather than 5pm. Footer and contact page both show hours.",
        "urgency": "high",
        "impact": "low",
        "category": "content",
        "confidence": 0.95,
        "status": "open",
        "due_in_days": 3,
        "files": [("components/Footer.tsx", "Renders the opening-hours block", 0.61)],
    },
    {
        "email": "hours",
        "title": "Fix 'recieve' typo on the contact page",
        "description": "Spelling error in the contact form confirmation copy. Same thread as the hours change.",
        "urgency": "medium",
        "impact": "low",
        "category": "content",
        "confidence": 0.97,
        "status": "open",
        "due_in_days": None,
        "files": [("app/contact/page.tsx", "Contains the confirmation copy", 0.58)],
    },
    {
        "email": "seo",
        "title": "Compress the homepage hero image",
        "description": (
            "A 3.2MB hero pushes mobile LCP to 4.8s. Serving a modern format at "
            "a sensible width should bring it under 2.5s."
        ),
        "urgency": "medium",
        "impact": "high",
        "category": "performance",
        "confidence": 0.81,
        "status": "open",
        "due_in_days": 14,
        "files": [("app/page.tsx", "Renders the hero image", 0.66)],
    },
    {
        "email": "testimonials",
        "title": "Add a patient testimonials section to the homepage",
        "description": "Requested as a nice-to-have. Needs copy from the client before any build work starts.",
        "urgency": "low",
        "impact": "medium",
        "category": "feature",
        "confidence": 0.74,
        "status": "open",
        "due_in_days": None,
        "files": [],
    },
]


def build_rows():
    emails, tasks = [], []
    for e in EMAILS:
        thread = f"{SEED_PREFIX}thread-{e['key']}"
        emails.append(
            {
                "gmail_message_id": f"{SEED_PREFIX}msg-{e['key']}",
                "thread_id": thread,
                "sender": e["sender"],
                "subject": e["subject"],
                "received_at": e["received_at"],
                "snippet": e["snippet"],
                "body_hash": hashlib.sha256(e["snippet"].encode()).hexdigest(),
                "is_actionable": True,
            }
        )
    for t in TASKS:
        thread = f"{SEED_PREFIX}thread-{t['email']}"
        due = None
        if t["due_in_days"] is not None:
            due = (NOW + timedelta(days=t["due_in_days"])).date().isoformat()
        tasks.append(
            {
                "email_key": t["email"],
                "row": {
                    "title": t["title"],
                    "description": t["description"],
                    "urgency": t["urgency"],
                    "impact": t["impact"],
                    "category": t["category"],
                    "status": t["status"],
                    "due_date": due,
                    "confidence": t["confidence"],
                    "notes": "",
                    "manually_edited": False,
                    "fingerprint": fingerprint(t["title"], thread),
                },
                "files": t["files"],
            }
        )
    return emails, tasks


def seed(url: str, key: str) -> None:
    email_rows, task_specs = build_rows()

    written = request(
        "POST",
        f"{url}/rest/v1/emails?on_conflict=gmail_message_id",
        key,
        email_rows,
        prefer="return=representation,resolution=merge-duplicates",
    )
    ids = {row["gmail_message_id"]: row["id"] for row in written}
    print(f"emails:     {len(written)}")

    task_rows = []
    for spec in task_specs:
        row = dict(spec["row"])
        row["email_id"] = ids[f"{SEED_PREFIX}msg-{spec['email_key']}"]
        task_rows.append(row)

    written_tasks = request(
        "POST",
        f"{url}/rest/v1/tasks?on_conflict=fingerprint",
        key,
        task_rows,
        prefer="return=representation,resolution=merge-duplicates",
    )
    by_fp = {row["fingerprint"]: row["id"] for row in written_tasks}
    print(f"tasks:      {len(written_tasks)}")

    file_rows = []
    for spec in task_specs:
        task_id = by_fp[spec["row"]["fingerprint"]]
        for path, reason, conf in spec["files"]:
            file_rows.append(
                {"task_id": task_id, "path": path, "reason": reason, "confidence": conf}
            )
    if file_rows:
        request(
            "POST",
            f"{url}/rest/v1/task_files?on_conflict=task_id,path",
            key,
            file_rows,
            prefer="return=minimal,resolution=merge-duplicates",
        )
    print(f"task_files: {len(file_rows)}")
    print("\nDone. Start the web app with `cd web && npm run dev`.")


def clear(url: str, key: str) -> None:
    # tasks and task_files cascade from emails.
    request(
        "DELETE",
        f"{url}/rest/v1/emails?gmail_message_id=like.{SEED_PREFIX}*",
        key,
        prefer="return=minimal",
    )
    print("demo rows deleted")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clear", action="store_true", help="delete the demo rows")
    args = parser.parse_args()
    url, key = load_env()
    (clear if args.clear else seed)(url, key)


if __name__ == "__main__":
    main()
