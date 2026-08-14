-- TaskFlow schema.
-- Apply with `python -m taskflow init-db`, or paste into the Supabase SQL editor.
-- Every statement is idempotent, so re-running is safe.

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------------------
-- Enums. Postgres has no `create type if not exists`, hence the guards.
-- ---------------------------------------------------------------------------
do $$ begin
  create type task_urgency as enum ('critical', 'high', 'medium', 'low');
exception when duplicate_object then null; end $$;

do $$ begin
  create type task_impact as enum ('high', 'medium', 'low');
exception when duplicate_object then null; end $$;

do $$ begin
  create type task_category as enum (
    'bug', 'content', 'feature', 'design', 'infra', 'performance', 'admin', 'unclear'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type task_status as enum ('open', 'in_progress', 'done', 'wont_do');
exception when duplicate_object then null; end $$;

-- ---------------------------------------------------------------------------
-- emails — one row per Gmail message we have looked at.
-- Rows are written even when an email yields no tasks, so a re-scan skips
-- work already paid for rather than re-billing the model for a newsletter.
-- ---------------------------------------------------------------------------
create table if not exists emails (
  id                uuid primary key default gen_random_uuid(),
  gmail_message_id  text not null unique,
  thread_id         text not null,
  sender            text not null,
  subject           text not null default '',
  received_at       timestamptz not null,
  snippet           text not null default '',
  body_hash         text not null,
  is_actionable     boolean not null default false,
  processed_at      timestamptz not null default now()
);

create index if not exists emails_received_at_idx on emails (received_at desc);
create index if not exists emails_thread_idx on emails (thread_id);

-- ---------------------------------------------------------------------------
-- tasks — the extracted work.
--
-- urgency and impact are stored separately rather than pre-collapsed into one
-- score, so the board can be re-sorted later without re-running extraction.
-- priority_bucket is generated from the pair; keep it in sync with
-- taskflow.models.priority_bucket, which mirrors this logic for --dry-run.
--
-- manually_edited records that a human overrode the model. pipeline.py refuses
-- to overwrite urgency/impact/status on those rows, so your corrections
-- survive every future scan.
-- ---------------------------------------------------------------------------
create table if not exists tasks (
  id              uuid primary key default gen_random_uuid(),
  email_id        uuid not null references emails (id) on delete cascade,
  title           text not null,
  description     text not null default '',
  urgency         task_urgency not null,
  impact          task_impact not null,
  category        task_category not null,
  priority_bucket text generated always as (
    case
      when urgency in ('critical', 'high') and impact = 'high' then 'do_now'
      when urgency in ('critical', 'high')                     then 'quick_win'
      when impact = 'high'                                     then 'schedule'
      else                                                          'backlog'
    end
  ) stored,
  status          task_status not null default 'open',
  due_date        date,
  confidence      real not null default 0.5 check (confidence between 0 and 1),
  notes           text not null default '',
  manually_edited boolean not null default false,
  fingerprint     text not null unique,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

create index if not exists tasks_status_idx on tasks (status);
create index if not exists tasks_bucket_idx on tasks (priority_bucket);
create index if not exists tasks_email_idx on tasks (email_id);

-- ---------------------------------------------------------------------------
-- task_files — suggested edit locations.
-- Paths are validated against the repo index before insert; nothing in this
-- system ever opens, reads, or writes them. They are display strings.
-- ---------------------------------------------------------------------------
create table if not exists task_files (
  id         uuid primary key default gen_random_uuid(),
  task_id    uuid not null references tasks (id) on delete cascade,
  path       text not null,
  reason     text not null default '',
  confidence real not null default 0.5 check (confidence between 0 and 1),
  created_at timestamptz not null default now(),
  unique (task_id, path)
);

create index if not exists task_files_task_idx on task_files (task_id);

-- ---------------------------------------------------------------------------
-- scan_runs — history, and the watermark for incremental Gmail queries.
-- covered_through_epoch is the point the next scan resumes from. Only
-- successful runs advance it, so a crashed scan re-reads its window instead of
-- silently skipping mail.
-- ---------------------------------------------------------------------------
create table if not exists scan_runs (
  id                    uuid primary key default gen_random_uuid(),
  started_at            timestamptz not null default now(),
  finished_at           timestamptz,
  emails_seen           integer not null default 0,
  emails_skipped        integer not null default 0,
  tasks_created         integer not null default 0,
  tasks_updated         integer not null default 0,
  covered_through_epoch bigint,
  error                 text
);

create index if not exists scan_runs_started_idx on scan_runs (started_at desc);

-- ---------------------------------------------------------------------------
-- updated_at maintenance
-- ---------------------------------------------------------------------------
create or replace function set_updated_at() returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists tasks_set_updated_at on tasks;
create trigger tasks_set_updated_at
  before update on tasks
  for each row execute function set_updated_at();

-- ---------------------------------------------------------------------------
-- Row level security.
--
-- No policies are defined, so the anon and authenticated roles can read
-- nothing. Both the scanner (direct Postgres connection) and the web app
-- (service-role key, server-side only) bypass RLS. The effect: if the anon key
-- ever leaks or the app is deployed publicly, these tables stay unreadable
-- until you deliberately add a policy.
-- ---------------------------------------------------------------------------
alter table emails     enable row level security;
alter table tasks      enable row level security;
alter table task_files enable row level security;
alter table scan_runs  enable row level security;
