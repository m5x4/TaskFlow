"""Command line interface.

    python -m taskflow doctor      check configuration and connectivity
    python -m taskflow auth        one-time Gmail consent
    python -m taskflow init-db     apply migrations
    python -m taskflow scan        fetch new mail and extract tasks
    python -m taskflow list        show open tasks
    python -m taskflow show <id>   full detail for one task
    python -m taskflow done <id>   mark a task done
    python -m taskflow map         backfill file suggestions
"""

from __future__ import annotations

import logging
import sys

import click

from . import db, gmail
from .config import ConfigError, load_settings
from .models import BUCKET_ORDER
from .pipeline import backfill_files, describe_task, run_scan
from .repo_index import build_index

BUCKET_LABELS = {
    "do_now": "DO NOW",
    "quick_win": "QUICK WIN",
    "schedule": "SCHEDULE",
    "backlog": "BACKLOG",
}

BUCKET_COLORS = {
    "do_now": "red",
    "quick_win": "yellow",
    "schedule": "cyan",
    "backlog": "white",
}


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )
    # These libraries are chatty at INFO and drown out our own output.
    logging.getLogger("googleapiclient").setLevel(logging.ERROR)
    logging.getLogger("google_auth_oauthlib").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # The genai SDK announces automatic function calling on every request. We
    # pass no tools, so the notice is pure noise.
    logging.getLogger("google_genai").setLevel(logging.WARNING)


def _fail(message: str) -> None:
    click.secho(f"error: {message}", fg="red", err=True)
    sys.exit(1)


def _suggest_model(listed: list[str], current: str) -> str | None:
    """Pick a plausible replacement when the configured model fails.

    Ordering is deliberate. `models.list` returns retired 2.x names that 404 on
    use, so suggesting the first "flash" match sends you to a dead model. The
    "lite" variants are also skipped: they were observed returning persistent
    504s while their full-size siblings answered in seconds.
    """
    def rank(name: str) -> tuple:
        return (
            "lite" in name,          # full-size first
            name.startswith("gemini-2"),  # 2.x is retired for new keys
            "preview" in name,
            "latest" not in name,    # rolling aliases outlive pinned versions
            name,
        )

    usable = [
        m
        for m in listed
        if "flash" in m
        and m != current
        and not any(x in m for x in ("image", "tts", "live", "thinking"))
    ]
    return min(usable, key=rank) if usable else None


def _human_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes}m {remainder}s" if remainder else f"{minutes}m"


def _value_state(value: str) -> str:
    """Distinguish a real value from a placeholder copied out of .env.example.

    Reporting "set" for a literal `sk-ant-...` is worse than reporting nothing:
    it sends you looking for the problem somewhere else.
    """
    if not value:
        return "MISSING"
    if value.endswith("...") or "YOUR-" in value.upper() or "xxxx" in value:
        return "PLACEHOLDER — still the example value"
    return "set"


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("-v", "--verbose", is_flag=True, help="Debug logging.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """Turn website task requests arriving by email into tracked work."""
    _configure_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


# ---------------------------------------------------------------------------


@cli.command()
def doctor() -> None:
    """Check configuration, database, Gmail, and repo index."""
    ok = True

    try:
        settings = load_settings(require_db=False, require_api_key=False)
    except ConfigError as exc:
        _fail(str(exc))
        return

    click.echo("Configuration")
    for label, value in [
        ("DATABASE_URL", _value_state(settings.database_url)),
        ("GEMINI_API_KEY", _value_state(settings.gemini_api_key)),
        ("model", settings.gemini_model),
        ("credentials.json", "found" if settings.gmail_credentials_path.exists() else "MISSING"),
        ("token.json", "found" if settings.gmail_token_path.exists() else "not yet — run `auth`"),
        ("OAuth redirect URI", gmail.redirect_uri(settings)),
        ("TARGET_REPO_PATH", str(settings.target_repo_path or "unset (file suggestions off)")),
    ]:
        missing = value == "MISSING"
        ok = ok and not missing
        click.secho(f"  {label:<20} {value}", fg="red" if missing else None)

    if settings.database_url:
        click.echo("\nDatabase")
        try:
            version = db.check_connection(settings)
            click.secho(f"  connected — {version.split(',')[0]}", fg="green")
        except Exception as exc:  # noqa: BLE001
            ok = False
            click.secho(f"  cannot connect: {exc}", fg="red")

    if _value_state(settings.gemini_api_key) == "set":
        click.echo("\nGemini")
        from google.genai import types

        from .extract import make_client

        # A diagnostic must fail fast. No retries and a short timeout, so a
        # rate-limited or stalled key is reported in seconds rather than
        # leaving doctor looking hung.
        client = make_client(settings, retries=1, timeout_seconds=20)

        # The API returns fully-qualified names ("models/gemini-3.5-flash");
        # GEMINI_MODEL is written bare, so compare on the bare form.
        try:
            listed = [
                (m.name or "").removeprefix("models/")
                for m in client.models.list()
                if m.supported_actions is None
                or "generateContent" in m.supported_actions
            ]
            click.secho(f"  key valid — {len(listed)} model(s) listed", fg="green")
        except Exception as exc:  # noqa: BLE001
            listed = []
            ok = False
            click.secho(f"  key rejected: {exc}", fg="red")

        # Listing is not proof: retired models still appear in models.list but
        # 404 on use ("no longer available to new users"). Only a real call
        # settles it, so spend a couple of tokens rather than fail mid-scan.
        if listed:
            try:
                client.models.generate_content(
                    model=settings.gemini_model,
                    contents="ok",
                    config=types.GenerateContentConfig(max_output_tokens=1000),
                )
                click.secho(f"  {settings.gemini_model} responds", fg="green")
            except Exception as exc:  # noqa: BLE001
                ok = False
                from .extract import is_quota_error

                if is_quota_error(exc):
                    # Not a broken key or a bad model name — the quota is spent.
                    # Scans handle this with pacing and backoff; doctor just
                    # reports it, since waiting here proves nothing.
                    click.secho(
                        f"  rate limited — the key and model are fine, the quota "
                        f"is currently exhausted",
                        fg="yellow",
                    )
                    click.secho(
                        f"  scans pace themselves at "
                        f"{settings.gemini_requests_per_minute}/min; lower "
                        f"GEMINI_REQUESTS_PER_MINUTE if this keeps happening",
                        fg="yellow",
                    )
                else:
                    click.secho(f"  {settings.gemini_model} unusable: {exc}", fg="red")
                    alt = _suggest_model(listed, settings.gemini_model)
                    if alt:
                        click.secho(f"  try GEMINI_MODEL={alt}", fg="yellow")

    if settings.gmail_token_path.exists():
        click.echo("\nGmail")
        try:
            service = gmail.build_service(settings)
            click.secho(f"  authorized as {gmail.account_email(service)} (read-only)", fg="green")
        except Exception as exc:  # noqa: BLE001
            ok = False
            click.secho(f"  cannot reach Gmail: {exc}", fg="red")

    if settings.target_repo_path:
        index = build_index(settings.target_repo_path, settings.max_index_files)
        click.echo("\nRepo index")
        if index.is_empty:
            click.secho(f"  nothing indexed at {settings.target_repo_path}", fg="yellow")
        else:
            click.secho(f"  {len(index.entries)} files indexed", fg="green")

    click.echo()
    if ok:
        click.secho("All checks passed.", fg="green")
    else:
        click.secho("Some checks failed — see above.", fg="yellow")
        sys.exit(1)


@cli.command()
@click.option("--force", is_flag=True, help="Re-run consent even if a token exists.")
def auth(force: bool) -> None:
    """Authorize Gmail access (read-only). Opens a browser once."""
    try:
        settings = load_settings(require_db=False, require_api_key=False)
        click.echo("Opening a browser for Google consent (read-only Gmail access)...")
        service = gmail.build_service(settings, force_auth=force)
        address = gmail.account_email(service)
    except ConfigError as exc:
        _fail(str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        _fail(f"authorization failed: {exc}")
        return

    click.secho(f"Authorized as {address}.", fg="green")
    click.echo(f"Token saved to {settings.gmail_token_path}")


@cli.command("init-db")
def init_db_cmd() -> None:
    """Create the database schema (idempotent)."""
    try:
        settings = load_settings(require_api_key=False)
        applied = db.init_db(settings)
    except ConfigError as exc:
        _fail(str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        _fail(f"migration failed: {exc}")
        return

    for name in applied:
        click.secho(f"applied {name}", fg="green")


@cli.command()
@click.option("--limit", type=int, help="Max emails this run (default from .env).")
@click.option("--dry-run", is_flag=True, help="Read and extract, but write nothing.")
def scan(limit: int | None, dry_run: bool) -> None:
    """Fetch new mail and extract tasks."""

    def start(total: int, skipped: int, per_minute: int, eta_seconds: float) -> None:
        if not total:
            return
        note = f"{total} email(s) to process"
        if skipped:
            note += f", {skipped} already seen"
        click.echo(note, err=True)
        if eta_seconds >= 30:
            # Below half a minute the pacing is not worth mentioning; above it,
            # silence for minutes looks like a hang.
            click.echo(
                f"Paced at {per_minute} request(s)/min to stay inside the quota "
                f"— roughly {_human_duration(eta_seconds)}.",
                err=True,
            )

    def progress(position: int, total: int, subject: str) -> None:
        click.echo(f"  [{position}/{total}] {subject[:70]}", err=True)

    try:
        settings = load_settings()
        summary = run_scan(
            settings,
            limit=limit,
            dry_run=dry_run,
            on_progress=progress,
            on_start=start,
        )
    except ConfigError as exc:
        _fail(str(exc))
        return
    except Exception as exc:  # noqa: BLE001
        _fail(f"scan failed: {exc}")
        return

    click.echo()
    if dry_run:
        click.secho("DRY RUN — nothing was written", fg="yellow", bold=True)
        if summary.preview:
            current = None
            for subject, task in summary.preview:
                if subject != current:
                    click.secho(f"\n{subject or '(no subject)'}", bold=True)
                    current = subject
                click.echo(f"  {describe_task(task)}")
                for f in task.files:
                    click.echo(f"      → {f.path}  ({f.reason})")
        else:
            click.echo("No tasks found in the emails scanned.")
        click.echo()

    click.echo(
        f"{summary.emails_seen} email(s) processed, "
        f"{summary.emails_skipped} already seen, "
        f"{summary.emails_actionable} actionable."
    )
    if not dry_run:
        click.echo(
            f"{summary.tasks_created} task(s) created, "
            f"{summary.tasks_updated} updated."
        )
    if summary.seconds_paced >= 30:
        click.echo(f"Spent {_human_duration(summary.seconds_paced)} pacing requests.")

    if summary.emails_deferred and not summary.aborted:
        # A capped run looks identical to a complete one from the counts alone.
        # Say what is still waiting, so the backlog is visible rather than
        # discovered later.
        click.echo(
            f"{summary.emails_deferred} more email(s) in this window than the "
            f"cap allows — scan again to continue from where this run stopped."
        )

    if summary.aborted:
        click.secho("\nScan stopped early", fg="yellow", bold=True)
        click.echo(f"  {summary.abort_reason}")
        if summary.emails_remaining:
            click.echo(f"  {summary.emails_remaining} email(s) not yet processed.")

    if summary.errors:
        click.secho(f"\n{len(summary.errors)} email(s) failed:", fg="yellow")
        for message in summary.errors[:10]:
            click.echo(f"  {message[:300]}")
        click.secho(
            "The scan watermark was not advanced — these will be retried next run.",
            fg="yellow",
        )


@cli.command("list")
@click.option("--all", "show_all", is_flag=True, help="Include done and dismissed.")
@click.option("--category", help="Filter by category.")
def list_cmd(show_all: bool, category: str | None) -> None:
    """Show tasks, grouped by priority bucket."""
    statuses = (
        ("open", "in_progress", "done", "wont_do") if show_all else ("open", "in_progress")
    )
    try:
        settings = load_settings(require_api_key=False)
        with db.connect(settings) as conn:
            rows = db.list_tasks(conn, statuses=statuses, category=category)
    except ConfigError as exc:
        _fail(str(exc))
        return

    if not rows:
        click.echo("No tasks. Run `scan` to check your mail.")
        return

    current = None
    for row in sorted(rows, key=lambda r: BUCKET_ORDER.get(r["priority_bucket"], 9)):
        bucket = row["priority_bucket"]
        if bucket != current:
            click.secho(
                f"\n{BUCKET_LABELS.get(bucket, bucket)}",
                fg=BUCKET_COLORS.get(bucket),
                bold=True,
            )
            current = bucket
        files = f"  [{row['file_count']} file(s)]" if row["file_count"] else ""
        status = "" if row["status"] == "open" else f"  <{row['status']}>"
        click.echo(f"  {str(row['id'])[:8]}  {row['title'][:64]}{files}{status}")
        click.echo(
            f"            {row['category']} · {row['urgency']} urgency · "
            f"{row['impact']} impact · from {row['sender'][:40]}"
        )
    click.echo()


@cli.command()
@click.argument("task_id")
def show(task_id: str) -> None:
    """Full detail for one task. Accepts an id prefix."""
    try:
        settings = load_settings(require_api_key=False)
        with db.connect(settings) as conn:
            task = _resolve_task(conn, task_id)
    except ConfigError as exc:
        _fail(str(exc))
        return

    if task is None:
        _fail(f"no task matching {task_id!r}")
        return

    click.secho(task["title"], bold=True)
    click.echo(f"  id         {task['id']}")
    click.echo(f"  bucket     {task['priority_bucket']}")
    click.echo(f"  urgency    {task['urgency']}")
    click.echo(f"  impact     {task['impact']}")
    click.echo(f"  category   {task['category']}")
    click.echo(f"  status     {task['status']}")
    click.echo(f"  confidence {task['confidence']:.2f}")
    if task["due_date"]:
        click.echo(f"  due        {task['due_date']}")
    if task["manually_edited"]:
        click.secho("  edited by hand — scans will not overwrite this", fg="cyan")

    click.echo(f"\n{task['description']}")

    if task["notes"]:
        click.secho("\nNotes", bold=True)
        click.echo(f"  {task['notes']}")

    click.secho("\nSource email", bold=True)
    click.echo(f"  from     {task['sender']}")
    click.echo(f"  subject  {task['subject']}")
    click.echo(f"  received {task['received_at']:%Y-%m-%d %H:%M}")
    click.echo(f"  snippet  {task['snippet'][:200]}")
    click.echo(
        f"  open     https://mail.google.com/mail/u/0/#inbox/{task['gmail_message_id']}"
    )

    click.secho("\nSuggested files", bold=True)
    if task["files"]:
        for f in task["files"]:
            click.echo(f"  {f['path']}  (confidence {f['confidence']:.2f})")
            click.echo(f"      {f['reason']}")
    else:
        click.echo("  none — set TARGET_REPO_PATH and run `map` to backfill")
    click.echo()


def _resolve_task(conn, task_id: str):
    """Look up by full id, falling back to a unique prefix match."""
    task = db.get_task(conn, task_id)
    if task:
        return task
    rows = db.list_tasks(conn, statuses=("open", "in_progress", "done", "wont_do"))
    matches = [r for r in rows if str(r["id"]).startswith(task_id)]
    if len(matches) == 1:
        return db.get_task(conn, str(matches[0]["id"]))
    if len(matches) > 1:
        click.secho(f"{task_id!r} matches {len(matches)} tasks — use more characters", fg="yellow")
    return None


@cli.command()
@click.argument("task_id")
@click.option(
    "--status",
    type=click.Choice(["open", "in_progress", "done", "wont_do"]),
    default="done",
    help="Status to set (default: done).",
)
def done(task_id: str, status: str) -> None:
    """Mark a task done, or set another status."""
    try:
        settings = load_settings(require_api_key=False)
        with db.connect(settings) as conn:
            task = _resolve_task(conn, task_id)
            if task is None:
                _fail(f"no task matching {task_id!r}")
                return
            db.set_status(conn, str(task["id"]), status)
    except ConfigError as exc:
        _fail(str(exc))
        return

    click.secho(f"{task['title']} → {status}", fg="green")


@cli.command("map")
@click.option("--repo", type=click.Path(exists=True, file_okay=False), help="Override TARGET_REPO_PATH.")
def map_cmd(repo: str | None) -> None:
    """Backfill file suggestions for open tasks that have none."""
    from pathlib import Path

    try:
        settings = load_settings(require_api_key=False)
        root = Path(repo).resolve() if repo else settings.target_repo_path
        if root is None:
            _fail("no repo path — pass --repo or set TARGET_REPO_PATH in .env")
            return

        index = build_index(root, settings.max_index_files)
        if index.is_empty:
            _fail(f"nothing indexable found in {root}")
            return

        click.echo(f"Indexed {len(index.entries)} files from {root}")
        considered, updated = backfill_files(settings, index)
    except ConfigError as exc:
        _fail(str(exc))
        return

    click.secho(
        f"{updated} of {considered} task(s) matched a file.", fg="green"
    )
    if considered and not updated:
        click.echo("No lexical overlap found — the repo may not match these tasks yet.")


if __name__ == "__main__":
    cli()
