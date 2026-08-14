-- Multi-user preparation.
--
-- Adds a nullable user_id to the three owned tables and rescopes both unique
-- constraints from global to per-user. Nothing here changes single-user
-- behaviour: user_id stays NULL, the scanner never sets it, and dedupe keeps
-- working exactly as before.
--
-- The point is timing. Adding a column to an empty table is free; rescoping a
-- unique constraint after real dedupe history exists means reconciling
-- collisions by hand. This is the cheap moment.
--
-- Still required before actually supporting multiple users, and deliberately
-- NOT done here: Supabase Auth wiring, encrypted per-user Gmail refresh tokens,
-- RLS policies (sketched at the bottom), a NOT NULL constraint on user_id, and
-- turning the scanner CLI into a per-user worker.

-- ---------------------------------------------------------------------------
-- Preconditions
-- ---------------------------------------------------------------------------

-- UNIQUE NULLS NOT DISTINCT arrived in PostgreSQL 15 and is load-bearing here:
-- see the note on the constraints below. Supabase provisions 15+, but fail
-- loudly rather than silently creating a constraint that does not dedupe.
do $$
begin
  if current_setting('server_version_num')::int < 150000 then
    raise exception
      'Migration 002 needs PostgreSQL 15 or newer (found %). Without '
      'NULLS NOT DISTINCT the rescoped unique constraints would stop '
      'deduplicating while user_id is NULL.',
      current_setting('server_version');
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 1. The ownership column
--
-- Nullable on purpose. Every existing row is yours; NULL reads as "the single
-- local user". Making it NOT NULL is a later migration, once sign-in exists and
-- there is a real auth.uid() to backfill with.
-- ---------------------------------------------------------------------------

alter table emails    add column if not exists user_id uuid;
alter table tasks     add column if not exists user_id uuid;
alter table scan_runs add column if not exists user_id uuid;

create index if not exists emails_user_idx    on emails    (user_id);
create index if not exists tasks_user_idx     on tasks     (user_id);
create index if not exists scan_runs_user_idx on scan_runs (user_id);

-- task_files is owned transitively through task_id and needs no column of its
-- own; its RLS policy will join through tasks.

-- ---------------------------------------------------------------------------
-- 2. Foreign keys to auth.users — Supabase only
--
-- The auth schema exists on Supabase but not on a plain Postgres container,
-- which is what scanner/test_db.py runs against. Add the constraint where it
-- makes sense and skip it where it cannot.
-- ---------------------------------------------------------------------------

do $$
declare
  t text;
begin
  if not exists (
    select 1 from information_schema.tables
     where table_schema = 'auth' and table_name = 'users'
  ) then
    raise notice
      'auth.users not found — skipping foreign keys. Expected on plain '
      'Postgres; on Supabase it means this ran somewhere unexpected.';
    return;
  end if;

  foreach t in array array['emails', 'tasks', 'scan_runs'] loop
    if not exists (
      select 1 from pg_constraint where conname = t || '_user_id_fkey'
    ) then
      execute format(
        'alter table %I add constraint %I foreign key (user_id) '
        'references auth.users(id) on delete cascade',
        t, t || '_user_id_fkey'
      );
    end if;
  end loop;
end $$;

-- ---------------------------------------------------------------------------
-- 3. Rescope the unique constraints
--
-- 001 made gmail_message_id and fingerprint globally unique, which is correct
-- for one user and wrong for several: two people receiving the same newsletter
-- share a Gmail message id, and the second insert would lose to a constraint
-- that has nothing to do with them.
--
-- NULLS NOT DISTINCT is what keeps this safe today. Postgres treats NULLs as
-- distinct in unique constraints by default, so a plain UNIQUE (user_id,
-- gmail_message_id) with user_id NULL everywhere would permit unlimited
-- duplicates — every re-scan would insert the same email again. NULLS NOT
-- DISTINCT makes two NULL user_ids compare equal, preserving exactly the
-- current single-user behaviour.
-- ---------------------------------------------------------------------------

alter table emails drop constraint if exists emails_gmail_message_id_key;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'emails_user_message_uniq'
  ) then
    alter table emails add constraint emails_user_message_uniq
      unique nulls not distinct (user_id, gmail_message_id);
  end if;
end $$;

alter table tasks drop constraint if exists tasks_fingerprint_key;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'tasks_user_fingerprint_uniq'
  ) then
    alter table tasks add constraint tasks_user_fingerprint_uniq
      unique nulls not distinct (user_id, fingerprint);
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 4. RLS policies — intentionally not created
--
-- RLS is already enabled on all four tables with no policies, so a publishable
-- or anon key reads nothing and the server-side secret key sees everything.
-- That is the correct posture for a single-user, server-rendered app.
--
-- These become necessary the moment a browser holds a publishable key. Left
-- here as the intended shape, not as dead code to enable casually — turning
-- them on without a working sign-in flow means auth.uid() is NULL, which with
-- NULLS NOT DISTINCT above would match every legacy row.
--
--   create policy "own emails" on emails
--     for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
--
--   create policy "own tasks" on tasks
--     for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
--
--   create policy "own scan runs" on scan_runs
--     for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
--
--   create policy "own task files" on task_files
--     for all using (
--       exists (select 1 from tasks t
--                where t.id = task_files.task_id and t.user_id = auth.uid())
--     );
--
-- A per-user Gmail token table is also needed. Refresh tokens are standing read
-- access to someone's inbox, so they must be encrypted with a key held outside
-- this database — not stored as plain text in a column here.
