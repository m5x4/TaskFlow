# TaskFlow

Reads Gmail, extracts website maintenance tasks with Gemini, stores them in
Supabase, shows them on a Next.js board sorted by urgency × impact.

Single user (iancherng@gmail.com). Local only. Built August 2026.

## Layout

- `scanner/` — Python CLI. Gmail (read-only OAuth) → Gemini → Postgres.
- `web/` — Next.js 15 App Router board. Server Components + Server Actions.
- `migrations/` — schema, source of truth. `init-db` applies all in filename order.

## Status

Working end-to-end. Real emails have been scanned into real tasks.

| Piece | State |
|---|---|
| Schema + migrations | Applied to Supabase (PG 17.6). 001, 002, 003 all live. |
| Gmail auth | Read-only, token stored — **expires every 7 days**, see below |
| Gemini extraction | Working — but see the model note below; the lite models 504 |
| Rate limiting | Paced + retrying. Live suite: 3 back-to-back calls, 0 rejections |
| Web board + detail | Working; verified against real data |
| CLI | `doctor`, `auth`, `init-db`, `scan`, `list`, `show`, `done`, `map` |
| Scheduled scan | launchd agent every 30 min, installed 11 Aug 2026 |
| Scan now button | Working — spawns the CLI, live progress bar, backlog count |
| Drag and drop | Working — drag a card between columns, or Shift+←/→ |

**Not done:** `TARGET_REPO_PATH` unset so file suggestions are off, no
deployment, no auth gate, frontend never tested below 1280px, no git history.

## Scan now (added 11 Aug 2026)

`startScan` in `web/app/actions.ts` spawns `.venv/bin/python -m taskflow scan`
detached. **This is the one piece that is local-only** — `spawn` needs the
scanner on the same machine, so deploying the board breaks this action and
nothing else.

Everything else travels through `scan_runs` (migration 003): the scanner writes
`progress_current` / `progress_total` / `progress_subject` / `heartbeat_at`
before each email, and `ScanPanel.tsx` polls a Server Action every 2s. That was
chosen over streaming stdout precisely so the progress bar survives the scanner
moving off this machine — only the spawn call would need replacing.

- **`progress_total` is the todo count, not the window.** A run listing 50
  messages with 49 already seen has 1 to do; a bar over the window would sit
  at 98% and look stuck.
- **`heartbeat_at` is what makes the lock releasable.** `finished_at` alone
  cannot distinguish a running scan from a killed one, and a crashed run would
  latch the button off forever. `isScanActive` in `web/lib/types.ts` treats a
  heartbeat older than 2 minutes as dead.
- **The lock matters now that launchd exists.** launchd will not double-start
  its own job but cannot see a button press. Two scanners racing is the
  watermark bug.
- **`emails_deferred` is the "not scanned yet" figure** — window total minus
  the capped slice, measured at scan time. The board cannot compute it live;
  only the scanner can ask Gmail. `fetchLastMeasuredScan` deliberately reads
  the last *completed* run with a non-null `backlog_total`, so a run that dies
  early reports a stale-but-true number instead of a confident zero.

Tests: `test_extract.py` 77 offline / 79 with `--live`; `test_db.py` 31.

## Drag and drop (added 11 Aug 2026)

Dragging a card to another column re-prioritises it. `Board.tsx` is a Client
Component wrapping the four `PriorityColumn`s; `moveTaskToBucket` in
`web/app/actions.ts` does the write, through the same `applyUpdate` as every
other edit, so a drop sets `manually_edited` like any other human decision.

- **A drop cannot write `priority_bucket`** — it is generated. It writes the
  urgency/impact pair that produces the target bucket, chosen by
  `priorityForBucket` in `web/lib/types.ts`. Twelve pairs map onto four
  buckets, so the inverse keeps whichever axis is already compatible: a
  critical/low task dropped on "Do now" stays critical and gains impact.
- **The action re-reads the row** rather than trusting urgency/impact from the
  browser. Preserving the untouched axis is only meaningful against the row of
  record; the board may be a stale snapshot.
- **`useOptimistic`, not `useState`.** The action calls `revalidatePath("/")`,
  so React holds the optimistic card until the re-rendered server props arrive
  and then drops it. Local state would have to guess when to stop and would
  flicker between the two answers.
- **Native HTML5 drag, no library** — whole-card drags between four fixed
  columns, no reordering within them. Columns accept a drop only when the drag
  carries `application/x-taskflow-task` (`web/lib/dnd.ts`), so a dragged link
  or file bounces off. It does not work on touch: Shift+←/→ on a focused card
  is the keyboard route, and the detail page keeps its priority selects.

## Rate limiting (fixed August 2026)

Scans pace themselves at `GEMINI_REQUESTS_PER_MINUTE` (default 15) via
`taskflow/ratelimit.py`, and the SDK's retry is now enabled in `make_client`.

The original bug: `google-genai` lists 429 as retryable but resolves to
`stop_after_attempt(1)` when no `retry_options` are passed — retry was simply
never on. Backoff starts at 5s (not the SDK's 1s default) because the SDK backs
off blindly and ignores the `retryDelay` Gemini returns; 5+10+20+40 ≈ 75s is
what it takes to outlast a per-minute quota window.

Scans abort early on a per-day quota error, or after 3 consecutive per-minute
rejections — the latter means `GEMINI_REQUESTS_PER_MINUTE` is above the model's
real limit. An aborted scan leaves the watermark untouched, so the next run
re-reads the same window.

Every Gemini client needs a timeout. Without one the SDK waits forever on a
stalled request; `doctor` hung for minutes this way.

## Known issue: the "lite" models return 504s

Observed August 2026: `gemini-3.5-flash-lite` and `gemini-flash-lite-latest`
both time out server-side (`504 DEADLINE_EXCEEDED`) even on "Say OK", at a 90s
timeout. Their full-size siblings answer in seconds.

| Model | Result | Free RPM |
|---|---|---|
| `gemini-3.5-flash` | works, 3.7s | 5 |
| `gemini-flash-latest` | works, 14.3s | — |
| `gemini-3.5-flash-lite` | **504 timeout** — but see below | 15 |
| `gemini-2.5-flash` | **404, retired** | — |
| `gemini-2.0-*` | 429 exhausted | — |

**Update 11 Aug 2026:** `doctor` got a clean live response from
`gemini-3.5-flash-lite`, so it appears to have recovered. `.env` is set to it at
15 RPM. Whether that holds under a full scan is untested — no scan has completed
since. If 504s return, switch to `gemini-3.5-flash` and drop
`GEMINI_REQUESTS_PER_MINUTE` to 5 in the same edit.

The lite models are the reason to want 15 RPM, so this is a real tradeoff:
full-size works but is capped at 5/min. **Keep
`GEMINI_REQUESTS_PER_MINUTE` matched to whichever model is set** — 5 for
`gemini-3.5-flash`, 15 for a lite model if they recover.

`models.list()` reports all of these as available, including the retired ones,
so listing is not proof — `doctor` makes a real call for that reason.

## Decisions worth not relitigating

- **Gemini, not Claude.** Free tier via AI Studio key. Do not propose Claude
  models here.
- **Urgency and impact are separate columns**, combined into `priority_bucket`
  by a Postgres generated column. Lets the board re-sort without re-extracting.
  The bucket logic exists twice: `migrations/001_init.sql` is authoritative,
  `taskflow/models.py:priority_bucket` mirrors it so `--dry-run` works without
  a database. Change one, change the other — and check
  `priorityForBucket` in `web/lib/types.ts`, which is the *inverse* map that
  card drags write through.
- **Scans read the oldest end of the window first.** Gmail lists newest-first,
  but `gmail.list_message_ids` pages the whole window and returns the *oldest*
  `MAX_EMAILS_PER_SCAN` of it, chronologically. The watermark can only advance
  across a contiguous run of processed mail, so a capped batch has to sit at
  the bottom of the window and grow upward. Taking the newest N leaves an
  unprocessed hole underneath that the next watermark skips for good — that bug
  stranded 23 emails in August 2026. `pipeline._covered_through` is the other
  half: a truncated scan stores the newest *processed* message's arrival time,
  not wall-clock time. Only a complete window may claim `scan_boundary`.
- **`manually_edited`** blocks scans from overwriting human edits. Enforced in
  two places, and both are needed: the `ON CONFLICT` in `db.py:upsert_task`
  refuses to overwrite urgency/impact when it is set, and `applyUpdate` in
  `web/app/actions.ts` sets it on every write the UI makes. Check both before
  touching upsert logic.
- **Server-side only.** No browser-side Supabase client, so no publishable key
  is needed. `lib/supabase.ts` imports `server-only`, which fails the build if a
  Client Component imports it.
- **RLS on, no policies.** Anon/publishable keys read nothing; only the secret
  key (server-side) and the scanner's direct Postgres connection can.
- **`user_id` exists but is always NULL** (migration 002), with both unique
  constraints scoped `UNIQUE NULLS NOT DISTINCT (user_id, ...)`. Prep for
  multi-user. The NULLS NOT DISTINCT is load-bearing — without it dedupe stops
  working entirely while user_id is NULL.

## Email is untrusted input

Anyone can email this inbox. Enforced structurally, not by prompt wording:

- Extraction call has no tools; output is schema-constrained JSON validated by
  Pydantic.
- Suggested file paths are whitelist-checked against the repo index. Nothing
  opens or reads them.
- The web app never renders email-derived HTML. No `dangerouslySetInnerHTML`.
- `scanner/fixtures/injection.txt` is a regression fixture that tries to induce
  a fraudulent payment task and SSH key exfiltration.

Do not weaken any of these.

## Commands

```bash
cd scanner
.venv/bin/python -m taskflow doctor          # checks config, DB, Gemini, Gmail
.venv/bin/python -m taskflow scan --dry-run  # reads mail, writes nothing
.venv/bin/python -m taskflow list
.venv/bin/python test_extract.py             # offline, 30 tests
```

```bash
cd web && npm run dev                        # localhost:3000
```

`test_db.py` needs a scratch Postgres and `TASKFLOW_TEST_DB=1`. It writes test
rows and expects an empty schema — never point it at Supabase.

## Gotchas

- Only run one dev server at a time. Two Next servers sharing `web/.next`
  corrupt each other's CSS hashes and produce 404s on `layout.css`.
- `DATABASE_URL` must be the **session pooler** (port 5432), not the transaction
  pooler (6543) which breaks psycopg3's prepared statements.
- Password special characters (`@ : / ? #`) must be percent-encoded in
  `DATABASE_URL`.
- **The Gmail refresh token expires every 7 days** while the OAuth consent
  screen is in **Testing** publishing status. Scans then fail with
  `invalid_grant: Token has been expired or revoked` — policy for unverified
  apps, not a bug. `auth --force` buys another 7 days; publishing the app (not
  the same as verification) stops the clock. This is the single thing most
  likely to break the scheduled scan. See Scheduling in the README.
- `token.json`'s mtime is the last *refresh*, not when the grant was issued, so
  it is not a usable age for the token.
- launchd reads the plist once at load. After editing it, `bootout` then
  `bootstrap` — a plain `kickstart` reruns the old definition.
- Without `PYTHONUNBUFFERED=1` in the plist, Python block-buffers stdout to the
  log file and a running scan looks hung — the log stays empty until it exits.
