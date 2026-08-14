"""Typed shapes shared across the pipeline.

The `Extracted*` models double as the model's output schema: they are handed to
Gemini as `response_schema`, so the response can only be JSON in this shape and
anything reaching the database has already passed Pydantic. That validation
boundary is the reason email text can never turn into an unexpected field value.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Urgency = Literal["critical", "high", "medium", "low"]
Impact = Literal["high", "medium", "low"]
Category = Literal[
    "bug", "content", "feature", "design", "infra", "performance", "admin", "unclear"
]
Status = Literal["open", "in_progress", "done", "wont_do"]

# Ordering used by the CLI. The web app sorts in SQL instead.
URGENCY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
BUCKET_ORDER = {"do_now": 0, "quick_win": 1, "schedule": 2, "backlog": 3}


# These models are sent to the API as a JSON schema, so they stay in the plain
# subset that structured outputs supports: no format keywords, no numeric
# bounds. Range and date validity are enforced in Python instead, by
# clamp_confidence and parse_due_date below, before anything reaches the
# database's own CHECK constraints.


class FileSuggestion(BaseModel):
    """A file the model believes the task touches. Never opened or executed."""

    path: str = Field(description="Repo-relative path, exactly as shown in the index")
    reason: str = Field(description="One sentence on why this file is implicated")
    confidence: float = Field(description="0.0 to 1.0")


class ExtractedTask(BaseModel):
    title: str = Field(description="Imperative one-liner, under 100 characters")
    description: str = Field(
        description="What needs doing and any specifics the sender gave"
    )
    urgency: Urgency = Field(description="How soon this must happen")
    impact: Impact = Field(description="How much value doing it delivers")
    category: Category
    due_date: str | None = Field(
        default=None,
        description="YYYY-MM-DD, only if the sender stated a deadline; else null",
    )
    confidence: float = Field(
        description="0.0 to 1.0 — confidence this is a real, actionable request"
    )
    files: list[FileSuggestion] = Field(
        default_factory=list,
        description="Empty when no repo index was provided or nothing matches",
    )


class ExtractionResult(BaseModel):
    """Top-level model output. An empty task list is a valid, expected answer."""

    is_actionable: bool = Field(
        description="False for newsletters, receipts, chatter, and anything "
        "that asks nothing of the maintainer"
    )
    reasoning: str = Field(
        description="One sentence on why this email does or does not contain work"
    )
    tasks: list[ExtractedTask] = Field(default_factory=list)


def clamp_confidence(value: float) -> float:
    """Force a confidence into [0, 1].

    The schema no longer carries numeric bounds, and the database has a CHECK
    constraint — clamping here means an out-of-range value from the model is a
    non-event instead of a failed scan.
    """
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


def parse_due_date(value: str | None) -> date | None:
    """Accept a YYYY-MM-DD string, ignore anything else.

    A malformed date is not worth failing a scan over; the task is still
    useful without one.
    """
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except (ValueError, AttributeError):
        return None


@dataclass
class EmailMessage:
    """A fetched Gmail message, normalized and truncated."""

    gmail_message_id: str
    thread_id: str
    sender: str
    subject: str
    received_at: datetime
    snippet: str
    body: str

    @property
    def body_hash(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()


def priority_bucket(urgency: str, impact: str) -> str:
    """Collapse the urgency x impact grid into one of four working buckets.

    Kept in Python as well as SQL so `--dry-run` can show buckets without a
    database round-trip. The SQL generated column in migrations/001_init.sql is
    the authority; this must agree with it.
    """
    urgent = urgency in ("critical", "high")
    valuable = impact == "high"
    if urgent and valuable:
        return "do_now"
    if urgent:
        return "quick_win"
    if valuable:
        return "schedule"
    return "backlog"


_WHITESPACE = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]")


def fingerprint(title: str, thread_id: str) -> str:
    """Stable identity for a task, so re-scans update rather than duplicate.

    Normalizing collapses case, punctuation, and spacing: "Fix the footer link"
    and "fix  THE  footer link!!" collide, which is what we want when someone
    follows up in the same thread. Wording is not normalized — a restatement
    that changes the words ("the footer link is broken") is a different
    fingerprint and becomes a second task.

    Scoping to thread_id means an unrelated email asking for something similar
    still gets its own task.
    """
    normalized = _NON_ALNUM.sub("", title.lower())
    normalized = _WHITESPACE.sub(" ", normalized).strip()
    return hashlib.sha256(f"{thread_id}::{normalized}".encode("utf-8")).hexdigest()
