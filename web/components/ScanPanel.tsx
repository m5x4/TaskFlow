"use client";

import { useCallback, useEffect, useRef, useState, useTransition } from "react";

import { getScanStatus, refreshBoard, startScan } from "@/app/actions";
import { isScanActive, type ScanRun } from "@/lib/types";

/*
 * The only Client Component on the board.
 *
 * It exists because a scan takes minutes: the request that starts one cannot
 * wait for it, so progress has to be pulled rather than rendered once. Polling
 * goes back through Server Actions because lib/supabase.ts imports `server-only`
 * — the secret key never reaches the browser.
 */

/** Poll cadence while a scan runs. Pacing is 12/min, so this is ample. */
const POLL_MS = 2000;

/**
 * Faster while waiting for a just-spawned scan to appear, so the handover from
 * the optimistic state to the real row is not visibly laggy.
 */
const STARTING_POLL_MS = 750;

/**
 * How long to believe a spawn that never produced a run row.
 *
 * Python has to boot, connect to Postgres, refresh the Gmail token and list the
 * window before it touches scan_runs. The connect dominates: psycopg is given
 * connect_timeout=15 and Supabase resolves to three addresses, so a network that
 * blocks 5432 takes ~45s to fail. Waiting past that means the scanner has had a
 * full chance to give up on its own — timing out at 45s would race it and blame
 * the app for a network fault.
 */
const START_TIMEOUT_MS = 90_000;

function percentOf(run: ScanRun): number {
  if (run.progress_total <= 0) return 0;
  return Math.min(
    100,
    Math.round((run.progress_current / run.progress_total) * 100),
  );
}

/**
 * Time left, derived from pacing alone.
 *
 * The scanner spaces calls at GEMINI_REQUESTS_PER_MINUTE, so the remaining work
 * has a knowable floor without measuring throughput. It reads low while a
 * request is retrying with backoff — the honest direction to be wrong, since
 * finishing early beats promising a time already missed.
 */
function etaLabel(run: ScanRun, perMinute = 12): string | null {
  const left = run.progress_total - run.progress_current;
  if (left <= 0) return null;
  const seconds = Math.round(left * (60 / perMinute));
  return seconds < 60 ? `~${seconds}s left` : `~${Math.round(seconds / 60)} min left`;
}

export function ScanPanel({
  initial,
  initialBacklog,
}: {
  initial: ScanRun | null;
  initialBacklog: number;
}) {
  const [run, setRun] = useState<ScanRun | null>(initial);
  const [active, setActive] = useState(() => isScanActive(initial));
  const [starting, setStarting] = useState(false);
  const [backlog, setBacklog] = useState(initialBacklog);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  // Separates "a scan finished while we watched" from a finished run that was
  // already on screen at load, so the board only refetches for the former.
  const wasBusy = useRef(active);

  /*
   * Handover from the spawned process to its database row.
   *
   * A scan is not visible the instant it starts: Python boots, refreshes the
   * Gmail token and lists the window before inserting into scan_runs. Polling
   * during that gap returns the *previous* run, so trusting it would report
   * "idle" for a scan that is very much alive.
   *
   * The row is recognised by id rather than by comparing timestamps: started_at
   * comes from Postgres and any deadline would come from the browser, and those
   * two clocks have no reason to agree.
   */
  const priorRunId = useRef<string | null>(null);
  const startedAt = useRef<number | null>(null);

  const poll = useCallback(async () => {
    try {
      const { run: next, active: liveActive } = await getScanStatus();
      setRun(next);

      let busy = liveActive;

      if (startedAt.current !== null) {
        const appeared = next !== null && next.id !== priorRunId.current;

        if (appeared) {
          // The real row is here — stop guessing and follow it, including the
          // case where a one-email scan already finished between two polls.
          startedAt.current = null;
          setStarting(false);
        } else if (Date.now() - startedAt.current > START_TIMEOUT_MS) {
          startedAt.current = null;
          setStarting(false);
          // Ordered by what actually happens. A blocked 5432 is the common
          // cause and the one the scanner cannot report itself, since writing
          // the error would need the connection that is failing.
          setError(
            "The scanner started but never reached the database. " +
              "Most likely it cannot reach Postgres on port 5432 — some " +
              "networks block it. Run `doctor` in scanner/ to confirm.",
          );
          busy = false;
        } else {
          busy = true; // still booting; hold the optimistic state
        }
      }

      setActive(busy);

      // Only a completed run has measured the backlog. Reading it from one
      // still in flight would show the figure from before it started work.
      if (next?.finished_at && next.backlog_total !== null) {
        setBacklog(next.emails_deferred);
      }

      if (wasBusy.current && !busy) {
        await refreshBoard();
      }
      wasBusy.current = busy;
    } catch {
      // A dropped poll is not worth surfacing: the next is seconds away, and
      // the scan is unaffected by the board losing contact.
    }
  }, []);

  useEffect(() => {
    if (!active && !starting) return;
    const id = setInterval(poll, starting ? STARTING_POLL_MS : POLL_MS);
    return () => clearInterval(id);
  }, [active, starting, poll]);

  function onScan() {
    setError(null);
    startTransition(async () => {
      const result = await startScan();
      if (!result.started) {
        setError(result.reason);
        return;
      }
      // Optimistic until the row shows up. Deliberately no immediate poll —
      // it would read the previous run and cancel this the tick after setting
      // it, which is exactly how the first version of this failed.
      priorRunId.current = run?.id ?? null;
      startedAt.current = Date.now();
      setStarting(true);
      setActive(true);
      wasBusy.current = true;
    });
  }

  const percent = run ? percentOf(run) : 0;
  const label =
    pending || starting ? "Starting…" : active ? "Scanning…" : "Scan now";

  // While starting, `run` is still the previous run — drawing the bar from it
  // would flash the last scan's progress, typically a full one.
  const following = active && !starting && run !== null;

  /*
   * A scan with nothing to do still opens a run row, so `following` alone would
   * render "0 / 0" behind a bar pinned at zero for the few seconds it takes to
   * list an empty window. Common in normal use — most scans find no new mail.
   */
  const hasWork = following && (run?.progress_total ?? 0) > 0;
  const checkingOnly = following && !hasWork;

  return (
    <div className="panel p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={onScan}
            disabled={active || pending}
            aria-busy={active || pending}
            className="btn disabled:cursor-not-allowed disabled:opacity-60"
          >
            {label}
          </button>

          {!active && !starting && (
            <span className="muted text-xs">
              {backlog > 0
                ? `${backlog} email${backlog === 1 ? "" : "s"} not scanned yet`
                : "Inbox is up to date"}
            </span>
          )}

          {checkingOnly && (
            <span className="muted text-xs">Checking for new mail…</span>
          )}
        </div>

        {hasWork && run && (
          <span className="muted text-xs tabular-nums">
            {run.progress_current} / {run.progress_total}
            {etaLabel(run) ? ` · ${etaLabel(run)}` : ""}
          </span>
        )}
      </div>

      {hasWork && run && (
        <div className="mt-2.5">
          <div
            role="progressbar"
            aria-valuenow={percent}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Scan progress"
            className="h-1.5 w-full overflow-hidden rounded-full bg-[#e3e6ea] dark:bg-[#262c36]"
          >
            <div
              className="h-full rounded-full bg-[#2f6fed] transition-[width] duration-500"
              style={{ width: `${percent}%` }}
            />
          </div>
          {run.progress_subject && (
            <p className="muted mt-1.5 truncate text-xs">
              {run.progress_subject}
            </p>
          )}
        </div>
      )}

      {error && (
        <p className="mt-2 text-xs text-[#b91c1c] dark:text-[#f87171]">{error}</p>
      )}
    </div>
  );
}
