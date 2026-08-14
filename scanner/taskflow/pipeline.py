"""Scan orchestration: fetch → extract → persist."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import db, gmail
from .config import Settings
from .extract import extract_tasks, is_daily_quota_error, is_quota_error, make_client
from .models import ExtractedTask, fingerprint, priority_bucket
from .ratelimit import RateLimiter
from .repo_index import RepoIndex, build_index

log = logging.getLogger(__name__)


# Consecutive per-minute rejections that mean pacing is misconfigured rather
# than unlucky. With pacing and retry both working this should be unreachable;
# reaching it means GEMINI_REQUESTS_PER_MINUTE is above the model's real limit.
MAX_CONSECUTIVE_QUOTA_ERRORS = 3


def abort_reason(
    exc: BaseException, consecutive_quota_errors: int, settings: Settings
) -> str | None:
    """Why the scan should stop here, or None to keep going.

    Some failures make the rest of the batch pointless — continuing just burns
    quota to rediscover the same rejection. Stopping leaves the watermark
    untouched, so the next scan re-reads this same window and nothing is lost.

    Pulled out of the scan loop so it can be tested without a live inbox.
    """
    if is_daily_quota_error(exc):
        return (
            "Gemini's daily quota is exhausted. The remaining emails would all "
            "fail, so the scan stopped. Nothing was lost — the next scan resumes "
            "from the same point once the quota resets."
        )

    if consecutive_quota_errors >= MAX_CONSECUTIVE_QUOTA_ERRORS:
        return (
            f"{consecutive_quota_errors} rate-limit rejections in a row despite "
            f"pacing at {settings.gemini_requests_per_minute}/min. "
            f"GEMINI_REQUESTS_PER_MINUTE is probably above what "
            f"{settings.gemini_model} actually allows — lower it in scanner/.env "
            f"and scan again."
        )

    return None


@dataclass
class ScanSummary:
    emails_seen: int = 0
    emails_skipped: int = 0
    emails_actionable: int = 0
    tasks_created: int = 0
    tasks_updated: int = 0
    errors: list[str] = field(default_factory=list)
    preview: list[tuple[str, ExtractedTask]] = field(default_factory=list)
    dry_run: bool = False
    aborted: bool = False
    abort_reason: str | None = None
    emails_remaining: int = 0
    emails_deferred: int = 0
    seconds_paced: float = 0.0

    @property
    def ok(self) -> bool:
        return not self.errors


def run_scan(
    settings: Settings,
    *,
    limit: int | None = None,
    dry_run: bool = False,
    on_progress=None,
    on_start=None,
) -> ScanSummary:
    """Process new mail into tasks.

    A dry run still reads Gmail and calls the model — that is the part worth
    previewing — but writes nothing and does not advance the scan watermark,
    so the same emails are picked up again on the next real scan.
    """
    summary = ScanSummary(dry_run=dry_run)
    max_emails = limit or settings.max_emails_per_scan

    index = build_index(settings.target_repo_path, settings.max_index_files)
    if index.is_empty:
        log.info("No repository indexed — tasks will have no file suggestions.")
    else:
        log.info("Indexed %d files from %s", len(index.entries), index.root)

    service = gmail.build_service(settings)
    client = make_client(settings)
    limiter = RateLimiter(settings.gemini_requests_per_minute)

    with db.connect(settings) as conn:
        since_epoch = db.last_covered_epoch(conn)
        query = gmail.build_query(settings, since_epoch)
        log.info("Gmail query: %s", query)

        # Capture the boundary before fetching. Anything that arrives mid-scan
        # falls after this mark and is picked up next time rather than being
        # skipped by a watermark set from the newest message we happened to see.
        scan_boundary = int(datetime.now(timezone.utc).timestamp())

        window = gmail.list_message_ids(service, query, max_emails)
        message_ids = window.ids
        already_seen = db.seen_message_ids(conn, message_ids)
        todo = [m for m in message_ids if m not in already_seen]
        summary.emails_skipped = len(message_ids) - len(todo)
        summary.emails_deferred = window.total - len(message_ids)

        if window.truncated:
            log.info(
                "Window holds %d message(s); processing the oldest %d and "
                "leaving %d for the next scan.",
                window.total,
                len(message_ids),
                summary.emails_deferred,
            )

        if on_start:
            # Pacing makes a large scan take minutes. Say so up front, so a
            # working scan is not mistaken for a hung one.
            on_start(
                len(todo),
                summary.emails_skipped,
                settings.gemini_requests_per_minute,
                limiter.estimate_seconds(len(todo)),
            )

        run_id = (
            None
            if dry_run
            else db.start_scan_run(
                conn, progress_total=len(todo), backlog_total=window.total
            )
        )
        consecutive_quota_errors = 0

        for position, message_id in enumerate(todo, start=1):
            try:
                email = gmail.fetch_message(service, message_id, settings.max_body_chars)
                if on_progress:
                    on_progress(position, len(todo), email.subject or "(no subject)")

                # Published before the Gemini call, so the board names the email
                # currently being worked on rather than the last one finished.
                if run_id is not None:
                    db.update_scan_progress(
                        conn, run_id, current=position, subject=email.subject
                    )

                result = extract_tasks(client, settings, email, index, limiter)
                consecutive_quota_errors = 0
                summary.emails_seen += 1
                if result.is_actionable and result.tasks:
                    summary.emails_actionable += 1

                if dry_run:
                    for task in result.tasks:
                        summary.preview.append((email.subject, task))
                    continue

                _persist(conn, email, result, summary)
                conn.commit()

            except Exception as exc:  # noqa: BLE001 — one bad email must not end the scan
                conn.rollback()
                message = f"{message_id}: {type(exc).__name__}: {exc}"
                summary.errors.append(message)
                log.warning("Skipping message %s", message)

                if is_quota_error(exc) and not is_daily_quota_error(exc):
                    consecutive_quota_errors += 1
                else:
                    consecutive_quota_errors = 0

                summary.abort_reason = abort_reason(
                    exc, consecutive_quota_errors, settings
                )

                if summary.abort_reason:
                    summary.aborted = True
                    summary.emails_remaining = len(todo) - position
                    log.warning("Aborting scan: %s", summary.abort_reason)
                    break

        summary.seconds_paced = limiter.total_waited

        if not dry_run and run_id is not None:
            covered = _covered_through(conn, summary, window, scan_boundary)
            db.finish_scan_run(
                conn,
                run_id,
                emails_seen=summary.emails_seen,
                emails_skipped=summary.emails_skipped,
                tasks_created=summary.tasks_created,
                tasks_updated=summary.tasks_updated,
                covered_through_epoch=covered,
                emails_deferred=summary.emails_deferred,
                error="; ".join(summary.errors[:5]) if summary.errors else None,
            )

    return summary


def _covered_through(
    conn,
    summary: ScanSummary,
    window: gmail.MessageWindow,
    scan_boundary: int,
) -> int | None:
    """How far this scan may claim coverage, or None to leave the watermark be.

    Three cases, and the middle one is the whole reason this is a function:

    - Something failed. Claim nothing. The window reopens next run, and
      `seen_message_ids` narrows the retry to just the messages that failed.
    - The cap left part of the window unread. Coverage stops at the newest
      message actually processed, so the remainder stays above the watermark
      and the next scan picks it up. Wall-clock time here would claim mail that
      was never read — the way a stale watermark strands a backlog for good.
    - The whole window was processed. Wall-clock time, captured before the
      fetch, so mail that landed mid-scan stays above the mark.
    """
    if not summary.ok:
        return None

    if not window.truncated:
        return scan_boundary

    # Every message in the batch is in `emails` by now — this run committed the
    # new ones, the rest were already there — so the newest arrival time in the
    # batch is exactly where contiguous coverage ends.
    return db.max_received_epoch(conn, window.ids)


def _persist(conn, email, result, summary: ScanSummary) -> None:
    """Write one email and its tasks. Caller commits."""
    email_id = db.upsert_email(conn, email, result.is_actionable)

    for task in result.tasks:
        task_id, created = db.upsert_task(
            conn, email_id, task, fingerprint(task.title, email.thread_id)
        )
        if created:
            summary.tasks_created += 1
        else:
            summary.tasks_updated += 1

        if task.files:
            db.replace_task_files(
                conn,
                task_id,
                [
                    {"path": f.path, "reason": f.reason, "confidence": f.confidence}
                    for f in task.files
                ],
            )


def backfill_files(settings: Settings, index: RepoIndex) -> tuple[int, int]:
    """Attach file suggestions to open tasks that have none.

    Used after the website code lands locally, so tasks captured before the
    repo existed still get pointed at the right files. Matching is lexical —
    task words against path and hint tokens — rather than a second model call,
    which keeps a backfill over a long backlog free.
    """
    if index.is_empty:
        return (0, 0)

    hints = {path: hint for path, hint in index.entries}
    considered = 0
    updated = 0

    with db.connect(settings) as conn:
        for task in db.tasks_needing_files(conn):
            considered += 1
            matches = _lexical_matches(task, hints)
            if not matches:
                continue
            db.replace_task_files(conn, task["id"], matches)
            updated += 1
        conn.commit()

    return considered, updated


_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "your", "our",
    "you", "are", "was", "not", "but", "all", "can", "has", "have", "should",
    "would", "please", "need", "needs", "make", "add", "fix", "update", "change",
    "page", "site", "website", "link", "text", "new", "old",
}


def _tokens(text: str) -> set[str]:
    return {
        word
        for word in "".join(c if c.isalnum() else " " for c in text.lower()).split()
        if len(word) > 2 and word not in _STOPWORDS
    }


def _lexical_matches(task: dict, hints: dict[str, str]) -> list[dict]:
    """Score indexed files against a task's wording. Best three, if any."""
    task_tokens = _tokens(f"{task['title']} {task['description']}")
    if not task_tokens:
        return []

    scored: list[tuple[int, str]] = []
    for path, hint in hints.items():
        overlap = task_tokens & _tokens(f"{path} {hint}")
        if overlap:
            scored.append((len(overlap), path))

    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    top = scored[:3]
    if not top:
        return []

    best = top[0][0]
    return [
        {
            "path": path,
            "reason": "Filename or contents overlap the task wording "
            "(lexical backfill, not model-reviewed)",
            # Scale against the strongest match so the numbers stay comparable,
            # capped low: this is a keyword guess, not a considered judgment.
            "confidence": round(min(0.6, 0.2 + 0.2 * (score / best)), 2),
        }
        for score, path in top
    ]


def describe_task(task: ExtractedTask) -> str:
    """One-line rendering used by --dry-run output."""
    bucket = priority_bucket(task.urgency, task.impact)
    return (
        f"[{bucket}] {task.title}  "
        f"({task.urgency}/{task.impact}, {task.category}, "
        f"conf {task.confidence:.2f})"
    )
