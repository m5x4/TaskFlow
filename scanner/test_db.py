"""Integration tests for db.py against a real Postgres.

WRITES TEST ROWS. Never point this at the database you actually use — run it
against a scratch database instead:

    docker run -d --name tf-pg -e POSTGRES_PASSWORD=postgres \\
        -e POSTGRES_DB=taskflow_test -p 55432:5432 postgres:16

    TASKFLOW_TEST_DB=1 \\
    DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/taskflow_test \\
    .venv/bin/python test_db.py

The suite assumes an empty schema and is not idempotent — several assertions
check first-insert behavior and the initial absence of a scan watermark. Drop
and recreate the database between runs.
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from taskflow import db
from taskflow.config import Settings
from taskflow.models import EmailMessage, ExtractedTask, FileSuggestion, fingerprint

if not os.environ.get("TASKFLOW_TEST_DB"):
    sys.exit(
        "Refusing to run: set TASKFLOW_TEST_DB=1 to confirm DATABASE_URL points at a\n"
        "scratch database. This suite writes test rows and expects an empty schema."
    )

if "DATABASE_URL" not in os.environ:
    sys.exit("DATABASE_URL is not set.")

S = Settings(
    database_url=os.environ["DATABASE_URL"], gemini_api_key="x", gemini_model="m",
    gemini_requests_per_minute=15, gemini_max_retries=5,
    gmail_credentials_path=Path("c"), gmail_token_path=Path("t"),
    gmail_oauth_port=8080, gmail_query="",
    max_emails_per_scan=50, max_body_chars=1000, initial_lookback_days=7,
    target_repo_path=None, max_index_files=400,
)

passed = failed = 0
def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1; print(f"  PASS  {label}")
    else:
        failed += 1; print(f"  FAIL  {label}" + (f" — {detail}" if detail else ""))

print("\nMigration idempotency")
db.init_db(S)
check("re-applying migrations does not error", True)

def mail(mid, thread, subject):
    return EmailMessage(
        gmail_message_id=mid, thread_id=thread, sender="Priya <p@example.org>",
        subject=subject, received_at=datetime.now(timezone.utc),
        snippet="snippet", body="body text",
    )

def task(title, urgency, impact, category="bug", files=None):
    return ExtractedTask(
        title=title, description="do the thing", urgency=urgency, impact=impact,
        category=category, confidence=0.9, files=files or [],
    )

with db.connect(S) as conn:
    print("\nInsert and dedupe")
    eid = db.upsert_email(conn, mail("m1", "t1", "Contact form broken"), True)
    tid, created = db.upsert_task(conn, eid, task("Fix contact form", "high", "high"),
                                  fingerprint("Fix contact form", "t1"))
    check("first insert reports created", created)

    tid2, created2 = db.upsert_task(conn, eid, task("fix  CONTACT form!", "low", "low"),
                                    fingerprint("fix  CONTACT form!", "t1"))
    check("restated title dedupes to same row", tid == tid2, f"{tid} vs {tid2}")
    check("second insert reports not-created", not created2)

    tid3, created3 = db.upsert_task(conn, eid, task("Fix contact form", "high", "high"),
                                    fingerprint("Fix contact form", "OTHER-THREAD"))
    check("same title in another thread is a new task", created3 and tid3 != tid)
    conn.commit()

    print("\nGenerated priority_bucket")
    cases = [("critical", "high", "do_now"), ("high", "low", "quick_win"),
             ("low", "high", "schedule"), ("low", "low", "backlog")]
    for i, (u, imp, expected) in enumerate(cases):
        t, _ = db.upsert_task(conn, eid, task(f"bucket case {i}", u, imp),
                              fingerprint(f"bucket case {i}", "t1"))
        row = db.get_task(conn, str(t))
        check(f"{u}/{imp} -> {expected}", row["priority_bucket"] == expected,
              f"got {row['priority_bucket']}")
    conn.commit()

    print("\nManual override survives a re-scan")
    t, _ = db.upsert_task(conn, eid, task("Override me", "low", "low"),
                          fingerprint("Override me", "t1"))
    fp = fingerprint("Override me", "t1")
    with conn.cursor() as cur:
        cur.execute("update tasks set urgency='critical', impact='high', "
                    "manually_edited=true where id=%s", (t,))
    conn.commit()
    db.upsert_task(conn, eid, task("Override me", "low", "low"), fp)
    conn.commit()
    row = db.get_task(conn, str(t))
    check("edited urgency not overwritten", row["urgency"] == "critical", row["urgency"])
    check("edited impact not overwritten", row["impact"] == "high", row["impact"])
    check("bucket follows the human values", row["priority_bucket"] == "do_now")

    print("\nUnedited task IS refreshed by a re-scan")
    t2, _ = db.upsert_task(conn, eid, task("Refresh me", "low", "low"),
                           fingerprint("Refresh me", "t1"))
    conn.commit()
    db.upsert_task(conn, eid, task("Refresh me", "critical", "high"),
                   fingerprint("Refresh me", "t1"))
    conn.commit()
    check("unedited urgency updates", db.get_task(conn, str(t2))["urgency"] == "critical")

    print("\nFile suggestions")
    db.replace_task_files(conn, tid, [
        {"path": "js/form.js", "reason": "handles submit", "confidence": 0.9},
        {"path": "contact.html", "reason": "the form markup", "confidence": 0.7},
    ])
    conn.commit()
    row = db.get_task(conn, str(tid))
    check("both files stored", len(row["files"]) == 2, str(len(row["files"])))
    check("sorted by confidence", row["files"][0]["path"] == "js/form.js")

    db.replace_task_files(conn, tid, [{"path": "css/main.css", "reason": "r", "confidence": 0.5}])
    conn.commit()
    row = db.get_task(conn, str(tid))
    check("replace swaps the whole set", [f["path"] for f in row["files"]] == ["css/main.css"])

    print("\nOut-of-range confidence is clamped before the CHECK constraint")
    t3, _ = db.upsert_task(conn, eid,
        ExtractedTask(title="Clamp me", description="d", urgency="low", impact="low",
                      category="bug", confidence=4.2), fingerprint("Clamp me", "t1"))
    conn.commit()
    check("confidence clamped to 1.0", db.get_task(conn, str(t3))["confidence"] == 1.0)

    print("\nEmail idempotency")
    again = db.upsert_email(conn, mail("m1", "t1", "Contact form broken"), True)
    conn.commit()
    check("same gmail id returns same row", again == eid)

    print("\nSeen-message filtering")
    seen = db.seen_message_ids(conn, ["m1", "m-unknown"])
    check("known id reported seen", "m1" in seen)
    check("unknown id not reported", "m-unknown" not in seen)
    check("empty input is safe", db.seen_message_ids(conn, []) == set())

    print("\nScan watermark")
    check("no watermark before any run", db.last_covered_epoch(conn) is None)
    rid = db.start_scan_run(conn)
    db.finish_scan_run(conn, rid, emails_seen=1, emails_skipped=0, tasks_created=1,
                       tasks_updated=0, covered_through_epoch=1700000000)
    check("successful run sets watermark", db.last_covered_epoch(conn) == 1700000000)
    rid2 = db.start_scan_run(conn)
    db.finish_scan_run(conn, rid2, emails_seen=1, emails_skipped=0, tasks_created=0,
                       tasks_updated=0, covered_through_epoch=None, error="boom")
    check("failed run does not advance watermark", db.last_covered_epoch(conn) == 1700000000)

    print("\nListing and status")
    rows = db.list_tasks(conn)
    check("list returns open tasks", len(rows) > 0)
    order = [r["priority_bucket"] for r in rows]
    rank = {"do_now": 0, "quick_win": 1, "schedule": 2, "backlog": 3}
    check("sorted by bucket", order == sorted(order, key=lambda b: rank[b]), str(order))
    check("file_count present", "file_count" in rows[0])

    db.set_status(conn, str(tid), "done")
    row = db.get_task(conn, str(tid))
    check("status set", row["status"] == "done")
    check("status change flags manual edit", row["manually_edited"])
    check("done task drops off default list",
          str(tid) not in [str(r["id"]) for r in db.list_tasks(conn)])

    print("\nBackfill worklist")
    need = db.tasks_needing_files(conn)
    check("only tasks lacking files are listed",
          all("id" in t for t in need) and len(need) > 0)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
