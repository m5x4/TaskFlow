-- Live scan progress, so the board can show a progress bar and a backlog count.
--
-- The scanner and the web app never talk to each other — they only share this
-- database. Progress therefore travels the same way task rows do: the scanner
-- updates its own scan_runs row as it works, and the board polls it. That keeps
-- a "Scan now" button working unchanged if the scanner ever moves off this
-- machine, since nothing here depends on the two being co-located.
--
-- All columns are additive with defaults, so existing rows and an older scanner
-- binary keep working against the new schema.

-- ---------------------------------------------------------------------------
-- 1. Per-email progress
--
-- progress_total is the size of the *todo* list, not the window: a scan that
-- lists 50 messages and finds 33 already processed has 17 to do, and a bar over
-- the window size would sit at 66% and look stuck.
-- ---------------------------------------------------------------------------

alter table scan_runs add column if not exists progress_current integer not null default 0;
alter table scan_runs add column if not exists progress_total   integer not null default 0;
alter table scan_runs add column if not exists progress_subject text;

-- Written on every progress update. A run whose heartbeat has gone quiet was
-- killed rather than finished — finished_at alone cannot tell the difference,
-- and without this a crashed scan would hold the "already running" lock
-- forever and disable the button permanently.
alter table scan_runs add column if not exists heartbeat_at timestamptz;

-- ---------------------------------------------------------------------------
-- 2. Backlog
--
-- backlog_total is every message matching the Gmail query at scan start —
-- the whole window, before MAX_EMAILS_PER_SCAN caps it.
--
-- emails_deferred is the part of that window this run could not reach. It is
-- the honest "not scanned yet" number: total minus the capped slice. It is a
-- measurement taken at scan time, not a live count, because only the scanner
-- can ask Gmail — the board has no Gmail credentials and never will.
-- ---------------------------------------------------------------------------

alter table scan_runs add column if not exists backlog_total   integer;
alter table scan_runs add column if not exists emails_deferred integer not null default 0;

-- ---------------------------------------------------------------------------
-- 3. Finding the in-flight run
--
-- The button's lock reads "newest row with finished_at null". Partial index so
-- that stays a cheap lookup as scan history grows.
-- ---------------------------------------------------------------------------

create index if not exists scan_runs_active_idx
  on scan_runs (started_at desc)
  where finished_at is null;
