# TaskFlow

Reads your Gmail for website work requests, extracts them into tracked tasks,
sorts them by urgency and impact, points at the files likely to need editing,
and shows the lot in a web app.

Two pieces sharing one Supabase database:

- **`scanner/`** — Python. Gmail (read-only) → Gemini → Postgres.
- **`web/`** — Next.js. The board you actually work out of.

---

## Layout

- **`scanner/taskflow/`** — the CLI. `gmail.py` fetches, `extract.py` makes the
  one model call, `models.py` holds the schema that call is constrained to,
  `db.py` owns every SQL statement, `pipeline.py` sequences the three.
- **`migrations/`** — the schema, and the source of truth for it. `init-db`
  applies them in filename order.
- **`web/lib/`** — server-only Supabase client, queries, shared types.
  `web/app/` is the board and the detail page; `web/app/actions.ts` is every
  write the UI can make.

Each module's docstring says what it is responsible for and why; start there
rather than here.

---

## What it does

A scan pulls mail newer than the last successful run, sends each message to
Gemini with a schema to fill in, and writes the results. Newsletters and
receipts produce nothing — that's a normal outcome, not a failure. Real
requests become tasks with an urgency, an impact, a category, and a confidence
score.

Urgency and impact are judged separately, then combined into one of four
buckets:

|                | High impact | Lower impact |
| -------------- | ----------- | ------------ |
| **Urgent**     | Do now      | Quick wins   |
| **Not urgent** | Schedule    | Backlog      |

Anything you change by hand is flagged `manually_edited`, and later scans will
not overwrite it.

---

## Setup

Three things need your accounts, so you have to do them yourself.

### 1. Supabase

1. Create a project at [supabase.com](https://supabase.com).
2. **Project Settings → Database → Connection string.** Copy the **Session
   pooler** URI (port `5432`). Not the transaction pooler on `6543` — it can't
   handle the prepared statements psycopg3 uses.
3. **Project Settings → API Keys.** Copy the project URL and a **secret** key
   (`sb_secret_...`). Not a publishable key — that one replaces `anon` and is
   for browser-side clients, which this app does not have.

   If your project still shows the legacy `anon` / `service_role` pair, the
   `service_role` key works too; `lib/supabase.ts` accepts either. Supabase
   deprecates the legacy keys at the end of 2026, and secret keys can be revoked
   individually, where rotating `service_role` invalidates everything at once.

### 2. Gmail

1. [Google Cloud Console](https://console.cloud.google.com) → create a project.
2. **APIs & Services → Library** → enable the **Gmail API**.
3. **OAuth consent screen** → External → add your own address as a test user.
4. **Credentials → Create credentials → OAuth client ID → Web application** →
   under **Authorized redirect URIs** add `http://localhost:8080/` (exactly,
   trailing slash included) → download the JSON as `scanner/credentials.json`.

If port 8080 is taken, set `GMAIL_OAUTH_PORT` in `.env` and register the
matching URI instead — the two must always agree, or consent fails with
`redirect_uri_mismatch`. `taskflow doctor` prints the URI it expects.

The scope requested is `gmail.readonly`. The token Google issues cannot send,
reply, label, archive, or delete — not by configuration, but because that
authority was never granted.

### 3. Gemini

Create an API key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey).

**Match `GEMINI_REQUESTS_PER_MINUTE` to your model.** The free-tier per-minute
quota differs per model — `gemini-3.5-flash` allows 5, the lite variants allow 15. Setting it too high is what makes a scan die partway through with
`429 RESOURCE_EXHAUSTED`. `taskflow doctor` makes a real call (not just
`models.list`, which happily reports retired models that 404 on use) to confirm
your model works before you rely on it.
`gemini-3.5-flash` is the default and runs on Google's free tier; set
`GEMINI_MODEL` in `.env` to use a different one.

### Then

```bash
cd scanner
cp .env.example .env          # fill in DATABASE_URL and GEMINI_API_KEY
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python -m taskflow doctor    # checks every piece of the above
.venv/bin/python -m taskflow init-db   # creates the tables
.venv/bin/python -m taskflow auth      # opens a browser once
```

```bash
cd ../web
cp .env.local.example .env.local       # fill in SUPABASE_URL + SUPABASE_SECRET_KEY
npm install
```

---

## Using it

```bash
cd scanner
.venv/bin/python -m taskflow scan --dry-run --limit 5   # preview, writes nothing
.venv/bin/python -m taskflow scan                       # for real
```

```bash
cd web
npm run dev            # http://localhost:3000
```

Other commands:

| Command     | Does                                           |
| ----------- | ---------------------------------------------- |
| `doctor`    | Checks config, database, Gmail, repo index     |
| `list`      | Open tasks grouped by bucket                   |
| `show <id>` | One task in full — accepts an id prefix        |
| `done <id>` | Mark done (`--status` for the others)          |
| `map`       | Backfill file suggestions after the repo lands |

### Scan now

The board has a **Scan now** button with a live progress bar and a count of
mail still waiting. It runs the same CLI — the Server Action spawns
`.venv/bin/python -m taskflow scan` detached, and the scanner reports progress
by updating its own `scan_runs` row, which the page polls.

Two consequences worth knowing:

- **It only works locally.** `spawn` needs the scanner on the same machine as
  the web server. Deploying the board disables this one action and nothing
  else, because everything else the board does is database reads and writes.
  Set `SCANNER_PATH` if the scanner is not at `../scanner`.
- **The button refuses while a scan is in flight.** Two scanners racing can
  advance the watermark past mail neither processed. launchd already refuses to
  double-start its own job, but it cannot see a button press. A scan whose
  heartbeat has been quiet for two minutes is treated as dead so a crash does
  not disable the button permanently.

The "N emails not scanned yet" figure is `emails_deferred` from the last
completed run — the part of the Gmail window that `MAX_EMAILS_PER_SCAN` cut
off. It is measured at scan time, not live: only the scanner has Gmail
credentials.

---

## File suggestions

Set `TARGET_REPO_PATH` in `scanner/.env` to your website's local path. The
scanner indexes it (honoring `.gitignore` via `git ls-files`) and includes the
tree in the prompt, so the model can name likely files.

**Suggested paths are checked against that index before being stored.** A path
the model invents is dropped rather than shown to you. Leaving
`TARGET_REPO_PATH` unset is fine — everything else works, tasks just carry no
file suggestions, and `taskflow map` fills them in later.

`map` uses keyword overlap rather than a second model call, so backfilling a
long backlog is free. Its suggestions are capped at 0.6 confidence and labelled
as lexical, because that's all they are.

---

## How untrusted email is handled

Anyone can email you, so email bodies are treated as hostile input throughout:

- **The extraction call has no tools.** Its only possible output is a Pydantic
  object. There is no path from email text to an executed action.
- **Email is fenced and labelled** as untrusted data in the prompt, and the
  system prompt states that instructions found inside it describe the email
  rather than direct the model.
- **All writes are parameterized.** Model output is bound as values, never
  interpolated into SQL.
- **Suggested paths are whitelist-checked** against the repo index, so
  `../../etc/passwd` cannot survive.
- **The web app never renders HTML from email** — no `dangerouslySetInnerHTML`
  anywhere, so React escapes everything.
- **The secret key stays server-side.** `lib/supabase.ts` imports `server-only`,
  so a Client Component importing it breaks the build instead of shipping the
  key. Neither variable is `NEXT_PUBLIC_`-prefixed.
- **RLS is on with no policies**, so a publishable or `anon` key reads nothing.
  Only the scanner's direct connection and the server-side secret-key client can
  see the data. Verified: the anon role gets
  `permission denied for table tasks`.

`scanner/fixtures/injection.txt` is a fixture that tries to talk the model into
creating a fraudulent payment task and exfiltrating SSH keys.
`test_extract.py --live` asserts it fails.

---

## Testing

```bash
cd scanner
.venv/bin/python test_extract.py          # offline, no API key needed
.venv/bin/python test_extract.py --live   # also calls the API (costs a little)
```

The offline suite covers bucket logic, fingerprinting, prompt assembly, and
path validation including traversal attempts. The live suite checks the model's
judgment on all three fixtures.

`test_db.py` is a database integration suite — dedupe, the generated
`priority_bucket` column, the manual-override guard, and the scan watermark. It
**writes test rows and expects an empty schema**, so it refuses to run without
`TASKFLOW_TEST_DB=1` and should be pointed at a scratch database, never the one
you use:

```bash
docker run -d --name tf-pg -e POSTGRES_PASSWORD=postgres \
    -e POSTGRES_DB=taskflow_test -p 55432:5432 postgres:16

TASKFLOW_TEST_DB=1 \
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:55432/taskflow_test \
.venv/bin/python test_db.py
```

It is not idempotent — drop and recreate the database between runs.

---

## Scheduling

Once `scan` works by hand, register a launchd agent so it runs on its own. The
plist lives at `~/Library/LaunchAgents/com.taskflow.scan.plist` and runs
`.venv/bin/python -m taskflow scan` every 30 minutes.

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.taskflow.scan.plist
launchctl kickstart -p gui/$(id -u)/com.taskflow.scan   # run one now
launchctl print gui/$(id -u)/com.taskflow.scan | grep -E "state|last exit"
launchctl bootout gui/$(id -u)/com.taskflow.scan        # remove
```

Editing the plist requires `bootout` then `bootstrap` again — launchd reads it
once at load.

Three things the plist has to get right:

- **An absolute path to `.venv/bin/python`.** launchd jobs get a minimal
  environment with no useful `PATH`.
- **`PYTHONUNBUFFERED=1`.** Python block-buffers stdout when it is a file
  rather than a terminal, so without this the log stays empty until a run
  exits and a working scan is indistinguishable from a hung one.
- **`StartInterval`, not `StartCalendarInterval`.** A missed interval fires
  once the Mac wakes, so an overnight sleep produces one catch-up scan rather
  than a gap.

Scans only run while the Mac is awake, and nothing is lost when it isn't: the
watermark advances only across mail actually processed, so a gap just means the
next scan reads a wider window. Backlog drains `MAX_EMAILS_PER_SCAN` per run,
oldest first.

launchd will not start a second copy of a job that is still running, which is
what keeps a scheduled run from colliding with a manual one. Two concurrent
scans are the one thing that can advance the watermark past unprocessed mail.

Logs go to `~/Library/Logs/taskflow-scan.log`. launchd does not rotate them and
48 runs a day adds up — truncate it occasionally or add a `newsyslog` entry.

### The scheduled job dies every 7 days until the OAuth app is published

While the OAuth consent screen sits in **Testing** publishing status, Google
expires the Gmail refresh token after 7 days. Every scan then fails with:

```
error: scan failed: ('invalid_grant: Token has been expired or revoked.')
```

That reads like a bug and is not one — it is the policy for unverified apps.
Re-authorizing buys exactly 7 more days:

```bash
.venv/bin/python -m taskflow auth --force
```

The durable fix is Cloud Console > APIs & Services > OAuth consent screen >
Publishing status > **Publish app**. That is not the same as submitting for
verification: the app stays unverified, the consent screen shows "Google hasn't
verified this app" once (Advanced > Continue), and refresh tokens stop expiring
on the 7-day clock. Verification only matters with real users — see Multi-user
below.

Note that `token.json`'s mtime is when it was last _refreshed_, not when the
refresh token was issued, so it is not a reliable age for the grant.

---

## Multi-user

Single user by design: no `user_id` in queries, no auth, no sessions, one Gmail
account. The security boundary is your machine.

`migrations/002_multiuser_prep.sql` does the one part that is expensive to
retrofit — it adds a nullable `user_id` to `emails`, `tasks`, and `scan_runs`,
and rescopes both unique constraints from global to per-user. Behaviour is
unchanged today: `user_id` stays NULL and the constraints are
`UNIQUE NULLS NOT DISTINCT`, so NULL still collides with NULL and dedupe works
exactly as before.

That detail is load-bearing. Postgres treats NULLs as distinct in unique
constraints by default, so a plain `UNIQUE (user_id, gmail_message_id)` with
`user_id` NULL everywhere would allow unlimited duplicates and every re-scan
would re-insert the same email.

Still needed before multiple users actually work:

- Supabase Auth with the Google provider, requesting `gmail.readonly` as an
  additional scope. The provider refresh token is returned **once**, only with
  `access_type=offline` and `prompt=consent`.
- A table for per-user Gmail refresh tokens, encrypted with a key held outside
  this database — each one is standing read access to somebody's inbox.
- RLS policies. The intended shape is sketched at the bottom of 002. Do not
  enable them before sign-in works: `auth.uid()` would be NULL, which with
  `NULLS NOT DISTINCT` matches every existing row.
- `user_id` made NOT NULL, once there is something to backfill it with.
- The scanner CLI becoming a per-user worker, with per-user cost limits — every
  user's mail runs through Gemini on your API key.

Google-side, `gmail.readonly` is a **sensitive** scope, not a restricted one, so
verification is Google's own review (free: privacy policy, homepage, demo video)
rather than a CASA Tier 2 audit. Restricted means `https://mail.google.com/`,
which this deliberately does not use. Until verified you are capped at 100
manually-added test users.

## Known limits

- **Dedupe is exact-match on a normalized title within a Gmail thread.** A
  follow-up that restates the same ask in different words creates a second
  task. Punctuation, case, and spacing differences are handled; rewording is
  not.
- **`map` matching is lexical.** It finds files whose names or contents share
  words with the task. It will miss a task whose wording doesn't resemble the
  code.
- **One model call per email.** Cost scales with inbox volume — narrow
  `GMAIL_QUERY` (for example with a label you apply yourself) if that matters.
  The repo index is cached across a scan, so only the first email in each run
  pays full price for it.
- **`npm audit` reports 3 high advisories** in `postcss` and `sharp`, both
  bundled inside Next.js itself. The advisory range covers every published Next
  release, so no upgrade clears them; `npm audit fix --force` would downgrade
  to Next 9. Both are build-time or image-pipeline dependencies that this app
  does not exercise.
