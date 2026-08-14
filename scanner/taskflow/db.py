"""Database access. Every query lives here; nothing else imports psycopg.

All writes are parameterized. Model output is passed as bound values, never
interpolated into SQL — that boundary is what keeps text from an untrusted
email from being able to influence a statement.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row

from .config import SCANNER_ROOT, ConfigError, Settings
from .models import EmailMessage, ExtractedTask, clamp_confidence, parse_due_date

MIGRATIONS_DIR = SCANNER_ROOT.parent / "migrations"


@contextmanager
def connect(settings: Settings) -> Iterator[psycopg.Connection]:
    """Open a connection with dict rows.

    Supabase's transaction pooler (port 6543) cannot handle the prepared
    statements psycopg3 starts using after a few executions. The session pooler
    (5432) is what .env.example recommends, but detect the other case and
    disable prepared statements rather than failing with a confusing
    "prepared statement already exists" error mid-scan.
    """
    try:
        port = urlparse(settings.database_url).port
    except ValueError as exc:
        # urlparse fails on the port when the password contains an unencoded
        # character that changes where the URI splits. Supabase generates
        # passwords that can include these, so this is a common first-run
        # failure and the raw error ("Port could not be cast to integer") gives
        # no hint about the cause.
        raise ConfigError(
            "DATABASE_URL could not be parsed. This usually means your database "
            "password contains a character that has meaning in a URL — most "
            "often @, but also : / ? # [ ] %.\n\n"
            "Percent-encode it in the password portion of the URI:\n"
            "    @ -> %40    : -> %3A    / -> %2F    ? -> %3F\n"
            "    # -> %23    [ -> %5B    ] -> %5D    % -> %25\n\n"
            "To encode yours without pasting it anywhere:\n"
            "    python3 -c \"import urllib.parse,getpass; "
            'print(urllib.parse.quote(getpass.getpass(\'password: \'), safe=\'\'))"\n\n'
            "Or reset it in Supabase (Settings > Database > Reset database "
            "password) and choose one with letters and digits only.\n\n"
            f"Underlying error: {exc}"
        ) from exc

    prepare_threshold = None if port == 6543 else 5

    conn = psycopg.connect(
        settings.database_url,
        row_factory=dict_row,
        prepare_threshold=prepare_threshold,
        connect_timeout=15,
    )
    try:
        yield conn
    finally:
        conn.close()


def check_connection(settings: Settings) -> str:
    """Verify the database is reachable. Returns the server version."""
    with connect(settings) as conn:
        with conn.cursor() as cur:
            cur.execute("select version() as v")
            row = cur.fetchone()
    return (row or {}).get("v", "unknown")


def init_db(settings: Settings) -> list[str]:
    """Apply every migration in order. Migrations are idempotent."""
    applied: list[str] = []
    paths = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not paths:
        raise FileNotFoundError(f"No migrations found in {MIGRATIONS_DIR}")

    with connect(settings) as conn:
        for path in paths:
            conn.execute(path.read_text())
            applied.append(path.name)
        conn.commit()
    return applied


# ---------------------------------------------------------------------------
# Scan bookkeeping
# ---------------------------------------------------------------------------


def last_covered_epoch(conn: psycopg.Connection) -> int | None:
    """Where the next scan should resume from.

    Only successful runs count, so a crashed scan re-reads its window rather
    than leaving a hole in coverage.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            select max(covered_through_epoch) as epoch
              from scan_runs
             where error is null and finished_at is not null
            """
        )
        row = cur.fetchone()
    return (row or {}).get("epoch")


def start_scan_run(
    conn: psycopg.Connection,
    *,
    progress_total: int = 0,
    backlog_total: int | None = None,
) -> uuid.UUID:
    """Open a run row. `progress_total` is the todo count, not the window size.

    Both numbers are known before the first Gemini call, so the board can draw a
    full progress bar and a backlog figure from the moment a scan starts rather
    than waiting for the first email to land.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into scan_runs (progress_total, backlog_total, heartbeat_at)
            values (%s, %s, now())
            returning id
            """,
            (progress_total, backlog_total),
        )
        run_id = cur.fetchone()["id"]
    conn.commit()
    return run_id


def update_scan_progress(
    conn: psycopg.Connection,
    run_id: uuid.UUID,
    *,
    current: int,
    subject: str | None,
) -> None:
    """Record position before the expensive call, and beat the heartbeat.

    Called before extraction rather than after, so the board shows what is being
    worked on now instead of what finished last — with pacing at 12/min there
    are five seconds of silence between emails to account for.

    Committed on its own because the caller's transaction covers one email and
    gets rolled back when that email fails; progress and the heartbeat have to
    survive that rollback or a run that hits an error looks dead to the lock.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            update scan_runs
               set progress_current = %s,
                   progress_subject = %s,
                   heartbeat_at = now()
             where id = %s
            """,
            (current, (subject or "")[:200] or None, run_id),
        )
    conn.commit()


def finish_scan_run(
    conn: psycopg.Connection,
    run_id: uuid.UUID,
    *,
    emails_seen: int,
    emails_skipped: int,
    tasks_created: int,
    tasks_updated: int,
    covered_through_epoch: int | None,
    emails_deferred: int = 0,
    error: str | None = None,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            update scan_runs
               set finished_at = now(),
                   emails_seen = %s,
                   emails_skipped = %s,
                   tasks_created = %s,
                   tasks_updated = %s,
                   covered_through_epoch = %s,
                   emails_deferred = %s,
                   error = %s,
                   heartbeat_at = now(),
                   progress_subject = null
             where id = %s
            """,
            (
                emails_seen,
                emails_skipped,
                tasks_created,
                tasks_updated,
                covered_through_epoch,
                emails_deferred,
                error,
                run_id,
            ),
        )
    conn.commit()


def seen_message_ids(conn: psycopg.Connection, message_ids: list[str]) -> set[str]:
    """Which of these Gmail messages have already been processed."""
    if not message_ids:
        return set()
    with conn.cursor() as cur:
        cur.execute(
            "select gmail_message_id from emails where gmail_message_id = any(%s)",
            (message_ids,),
        )
        return {r["gmail_message_id"] for r in cur.fetchall()}


def max_received_epoch(conn: psycopg.Connection, message_ids: list[str]) -> int | None:
    """Arrival time of the newest of these messages that has been processed.

    Read after a capped scan commits, when every message in the batch is in the
    table — some written by this run, the rest already there. The newest one's
    arrival time is then exactly how far coverage reaches, which is the
    watermark a truncated scan needs: wall-clock time would claim the part of
    the window the cap left behind.
    """
    if not message_ids:
        return None
    with conn.cursor() as cur:
        cur.execute(
            """
            select max(extract(epoch from received_at))::bigint as epoch
              from emails
             where gmail_message_id = any(%s)
            """,
            (message_ids,),
        )
        row = cur.fetchone()
    return (row or {}).get("epoch")


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def upsert_email(
    conn: psycopg.Connection, email: EmailMessage, is_actionable: bool
) -> uuid.UUID:
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into emails (gmail_message_id, thread_id, sender, subject,
                                received_at, snippet, body_hash, is_actionable)
                 values (%s, %s, %s, %s, %s, %s, %s, %s)
            -- Conflict target matches emails_user_message_uniq from migration
            -- 002. user_id is NULL for now; the constraint is NULLS NOT
            -- DISTINCT, so NULL still collides with NULL and dedupe holds.
            on conflict (user_id, gmail_message_id) do update
                    set is_actionable = excluded.is_actionable,
                        processed_at  = now()
              returning id
            """,
            (
                email.gmail_message_id,
                email.thread_id,
                email.sender,
                email.subject,
                email.received_at,
                email.snippet,
                email.body_hash,
                is_actionable,
            ),
        )
        return cur.fetchone()["id"]


def upsert_task(
    conn: psycopg.Connection,
    email_id: uuid.UUID,
    task: ExtractedTask,
    fingerprint: str,
) -> tuple[uuid.UUID, bool]:
    """Insert a task, or refresh an existing one with the same fingerprint.

    Returns (task_id, created). On conflict the description and category are
    refreshed from the newer email, but urgency, impact, and status are left
    alone when manually_edited is set — a human decision outranks a re-run.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            insert into tasks (email_id, title, description, urgency, impact,
                               category, due_date, confidence, fingerprint)
                 values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            -- Matches tasks_user_fingerprint_uniq from migration 002; see the
            -- note in upsert_email about the NULL user_id.
            on conflict (user_id, fingerprint) do update
                    set description = excluded.description,
                        category    = excluded.category,
                        due_date    = coalesce(excluded.due_date, tasks.due_date),
                        confidence  = excluded.confidence,
                        urgency     = case when tasks.manually_edited
                                           then tasks.urgency else excluded.urgency end,
                        impact      = case when tasks.manually_edited
                                           then tasks.impact else excluded.impact end
              returning id, (xmax = 0) as created
            """,
            (
                email_id,
                task.title,
                task.description,
                task.urgency,
                task.impact,
                task.category,
                parse_due_date(task.due_date),
                clamp_confidence(task.confidence),
                fingerprint,
            ),
        )
        row = cur.fetchone()
        return row["id"], row["created"]


def replace_task_files(
    conn: psycopg.Connection, task_id: uuid.UUID, files: list[dict[str, Any]]
) -> None:
    """Swap in a fresh set of file suggestions for a task."""
    with conn.cursor() as cur:
        cur.execute("delete from task_files where task_id = %s", (task_id,))
        for f in files:
            cur.execute(
                """
                insert into task_files (task_id, path, reason, confidence)
                     values (%s, %s, %s, %s)
                on conflict (task_id, path) do nothing
                """,
                (
                    task_id,
                    f["path"],
                    f["reason"],
                    clamp_confidence(f["confidence"]),
                ),
            )


# ---------------------------------------------------------------------------
# Reads (CLI)
# ---------------------------------------------------------------------------


def list_tasks(
    conn: psycopg.Connection,
    *,
    statuses: tuple[str, ...] = ("open", "in_progress"),
    category: str | None = None,
) -> list[dict[str, Any]]:
    sql = """
        select t.id, t.title, t.urgency, t.impact, t.category, t.priority_bucket,
               t.status, t.due_date, t.created_at, e.sender, e.subject,
               (select count(*) from task_files f where f.task_id = t.id) as file_count
          from tasks t
          join emails e on e.id = t.email_id
         where t.status = any(%s)
    """
    params: list[Any] = [list(statuses)]
    if category:
        sql += " and t.category = %s"
        params.append(category)
    sql += """
         order by case t.priority_bucket
                    when 'do_now'    then 0
                    when 'quick_win' then 1
                    when 'schedule'  then 2
                    else 3
                  end,
                  t.created_at desc
    """
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def get_task(conn: psycopg.Connection, task_id: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            select t.*, e.sender, e.subject, e.received_at, e.snippet,
                   e.gmail_message_id, e.thread_id
              from tasks t
              join emails e on e.id = t.email_id
             where t.id::text = %s
            """,
            (task_id,),
        )
        task = cur.fetchone()
        if not task:
            return None
        cur.execute(
            """
            select path, reason, confidence
              from task_files
             where task_id = %s
             order by confidence desc
            """,
            (task["id"],),
        )
        task["files"] = cur.fetchall()
    return task


def set_status(conn: psycopg.Connection, task_id: str, status: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            update tasks
               set status = %s, manually_edited = true
             where id::text = %s
            """,
            (status, task_id),
        )
        changed = cur.rowcount > 0
    conn.commit()
    return changed


def tasks_needing_files(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Open tasks with no file suggestions yet — the backfill worklist."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select t.id, t.title, t.description, t.category
              from tasks t
             where t.status in ('open', 'in_progress')
               and not exists (select 1 from task_files f where f.task_id = t.id)
             order by t.created_at desc
            """
        )
        return cur.fetchall()
