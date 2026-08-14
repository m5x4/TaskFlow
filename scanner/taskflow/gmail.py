"""Gmail access — read-only.

The OAuth scope is `gmail.readonly`, the narrowest scope that can list and read
messages. Nothing in this module can send, reply, label, archive, or delete;
the token Google issues simply does not carry that authority.
"""

from __future__ import annotations

import base64
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from .config import GMAIL_SCOPES, ConfigError, Settings
from .models import EmailMessage

log = logging.getLogger(__name__)

# Listing is ID-only and cheap, but a window this large means scans are running
# far apart or the lookback is set very wide. Worth saying out loud.
LARGE_WINDOW = 5000

_SCRIPT_STYLE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.I | re.S)
_BLOCK_END = re.compile(r"</(p|div|tr|li|h[1-6]|blockquote)>", re.I)
_BR = re.compile(r"<br\s*/?>", re.I)
_TAG = re.compile(r"<[^>]+>")
_BLANK_LINES = re.compile(r"\n{3,}")
_TRAILING_SPACE = re.compile(r"[ \t]+\n")


class GmailAuthError(RuntimeError):
    """Raised when credentials are missing or consent has not been granted."""


def redirect_uri(settings: Settings) -> str:
    """The callback URL that must be registered on the OAuth client.

    Google matches this against the client's Authorized redirect URIs
    character for character, trailing slash included, so it is derived in one
    place rather than written out wherever it happens to be needed.
    """
    return f"http://localhost:{settings.gmail_oauth_port}/"


def authorize(settings: Settings, force: bool = False) -> Credentials:
    """Load cached credentials, refreshing or running consent as needed.

    The consent step opens a browser and is the one part of setup that has to
    be done by hand. After it succeeds, the refresh token in token.json keeps
    scheduled scans working without further interaction.
    """
    creds: Credentials | None = None

    if settings.gmail_token_path.exists() and not force:
        creds = Credentials.from_authorized_user_file(
            str(settings.gmail_token_path), GMAIL_SCOPES
        )

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token and not force:
        creds.refresh(Request())
        settings.gmail_token_path.write_text(creds.to_json())
        return creds

    if not settings.gmail_credentials_path.exists():
        raise ConfigError(
            f"OAuth client file not found at {settings.gmail_credentials_path}.\n"
            "Create one in Google Cloud Console: APIs & Services > Credentials >\n"
            "Create credentials > OAuth client ID > Web application, add\n"
            f"{redirect_uri(settings)} as an Authorized redirect URI, then\n"
            "download the JSON to that path. See the README for the walkthrough."
        )

    flow = InstalledAppFlow.from_client_secrets_file(
        str(settings.gmail_credentials_path), GMAIL_SCOPES
    )
    # A Web application client requires the redirect URI to match a registered
    # one exactly, so the port is pinned rather than ephemeral. (Desktop-app
    # clients get a loopback exemption and can use port=0; this one cannot.)
    try:
        creds = flow.run_local_server(
            port=settings.gmail_oauth_port,
            # Web clients only return a refresh token when offline access is
            # asked for explicitly, and only on a consent screen the user
            # actually sees. Without both, token.json expires in an hour and
            # scheduled scans start failing with no obvious cause.
            access_type="offline",
            prompt="consent",
        )
    except OSError as exc:
        raise ConfigError(
            f"Port {settings.gmail_oauth_port} is not available for the OAuth "
            f"callback ({exc}).\nFree it, or set GMAIL_OAUTH_PORT to another "
            "port and add the matching\nhttp://localhost:<port>/ to the "
            "client's Authorized redirect URIs."
        ) from exc

    settings.gmail_token_path.write_text(creds.to_json())
    settings.gmail_token_path.chmod(0o600)
    return creds


def build_service(settings: Settings, force_auth: bool = False):
    creds = authorize(settings, force=force_auth)
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def account_email(service) -> str:
    return service.users().getProfile(userId="me").execute().get("emailAddress", "")


# ---------------------------------------------------------------------------
# Body extraction
# ---------------------------------------------------------------------------


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode("ascii")).decode("utf-8", "replace")


def html_to_text(raw: str) -> str:
    """Flatten HTML mail to readable text.

    Deliberately crude: scripts and styles are dropped, block ends become
    newlines, remaining tags are stripped. The output is only ever used as
    model input and display text, never rendered as markup.
    """
    text = _SCRIPT_STYLE.sub(" ", raw)
    text = _BR.sub("\n", text)
    text = _BLOCK_END.sub("\n", text)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    text = _TRAILING_SPACE.sub("\n", text)
    return _BLANK_LINES.sub("\n\n", text).strip()


def _collect_parts(payload: dict) -> tuple[str, str]:
    """Walk the MIME tree, returning (plain_text, html_text)."""
    plain_chunks: list[str] = []
    html_chunks: list[str] = []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        data = body.get("data")
        if data:
            if mime == "text/plain":
                plain_chunks.append(_decode(data))
            elif mime == "text/html":
                html_chunks.append(_decode(data))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    return "\n".join(plain_chunks).strip(), "\n".join(html_chunks).strip()


def extract_body(payload: dict, max_chars: int) -> str:
    """Best available plain-text rendering of the message, truncated."""
    plain, html_text = _collect_parts(payload)
    body = plain or (html_to_text(html_text) if html_text else "")
    if len(body) > max_chars:
        body = body[:max_chars] + "\n\n[... truncated ...]"
    return body


def _header(payload: dict, name: str) -> str:
    target = name.lower()
    for h in payload.get("headers", []) or []:
        if h.get("name", "").lower() == target:
            return h.get("value", "")
    return ""


def parse_message(raw: dict, max_chars: int) -> EmailMessage:
    payload = raw.get("payload", {}) or {}
    received = datetime.fromtimestamp(
        int(raw.get("internalDate", 0)) / 1000, tz=timezone.utc
    )
    return EmailMessage(
        gmail_message_id=raw["id"],
        thread_id=raw.get("threadId", raw["id"]),
        sender=_header(payload, "From"),
        subject=_header(payload, "Subject"),
        received_at=received,
        snippet=html.unescape(raw.get("snippet", "")),
        body=extract_body(payload, max_chars),
    )


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


def build_query(settings: Settings, since_epoch: int | None) -> str:
    """Base query plus an `after:` watermark.

    First run has no watermark, so it reaches back INITIAL_LOOKBACK_DAYS rather
    than pulling the entire mailbox.
    """
    if since_epoch is None:
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=settings.initial_lookback_days
        )
        since_epoch = int(cutoff.timestamp())
    return f"{settings.gmail_query} after:{since_epoch}".strip()


@dataclass(frozen=True)
class MessageWindow:
    """The slice of a Gmail query one scan will process.

    `ids` holds the *oldest* `limit` messages in the window, oldest first.
    Which end gets taken is load-bearing, not a detail: the watermark can only
    advance across a contiguous run of processed mail, so a capped batch has to
    start at the bottom of the window and grow upward. Taking the newest
    `limit` instead — which is what Gmail hands back by default — leaves an
    unprocessed hole underneath the batch, and any watermark written afterwards
    skips straight over it.
    """

    ids: list[str]
    total: int

    @property
    def truncated(self) -> bool:
        """Whether the cap left part of the window for a later scan."""
        return self.total > len(self.ids)


def list_message_ids(service, query: str, limit: int) -> MessageWindow:
    """The oldest `limit` messages matching `query`, oldest first.

    The whole window is listed even though only `limit` of it is returned,
    because Gmail pages newest-first and the messages this scan wants are on
    the last page. Listing costs one cheap ID-only call per 100 messages;
    extraction is the expensive part, and that stays capped at `limit`.
    """
    ids: list[str] = []
    page_token: str | None = None

    while True:
        response = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=100, pageToken=page_token)
            .execute()
        )
        ids.extend(m["id"] for m in response.get("messages", []) or [])
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    if len(ids) > LARGE_WINDOW:
        log.warning(
            "%d messages match the scan window. Scanning more often, or "
            "narrowing GMAIL_QUERY, would keep each run smaller.",
            len(ids),
        )

    chronological = list(reversed(ids))
    return MessageWindow(ids=chronological[:limit], total=len(chronological))


def fetch_message(service, message_id: str, max_chars: int) -> EmailMessage:
    raw = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute()
    )
    return parse_message(raw, max_chars)
