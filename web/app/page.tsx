import { Board } from "@/components/Board";
import { FilterBar } from "@/components/FilterBar";
import { ScanPanel } from "@/components/ScanPanel";
import { SetupNotice } from "@/components/SetupNotice";
import { fetchLastMeasuredScan, fetchLastScan, fetchTasks } from "@/lib/queries";
import { isConfigured } from "@/lib/supabase";
import { relativeTime, type Status, type Task } from "@/lib/types";

// Always read fresh: the scanner writes to this database out of band, so a
// cached board would go stale without the app ever knowing.
export const dynamic = "force-dynamic";

export default async function BoardPage({
  searchParams,
}: {
  searchParams: Promise<{ category?: string; all?: string }>;
}) {
  if (!isConfigured) {
    return (
      <SetupNotice
        title="Supabase is not configured"
        detail="The web app needs read access to the same database the scanner writes to."
        steps={[
          "Copy web/.env.local.example to web/.env.local",
          "Paste SUPABASE_URL and SUPABASE_SECRET_KEY from Supabase → Settings → API Keys",
          "Restart the dev server",
        ]}
      />
    );
  }

  const params = await searchParams;
  const showAll = params.all === "1";
  const statuses: Status[] = showAll
    ? ["open", "in_progress", "done", "wont_do"]
    : ["open", "in_progress"];

  let tasks: Task[];
  let lastScan: Awaited<ReturnType<typeof fetchLastScan>>;
  let measuredScan: Awaited<ReturnType<typeof fetchLastMeasuredScan>>;
  try {
    [tasks, lastScan, measuredScan] = await Promise.all([
      fetchTasks({ statuses, category: params.category }),
      fetchLastScan(),
      fetchLastMeasuredScan(),
    ]);
  } catch (error) {
    return (
      <SetupNotice
        title="Could not reach the database"
        detail={error instanceof Error ? error.message : String(error)}
        steps={[
          "Check that SUPABASE_URL and SUPABASE_SECRET_KEY in web/.env.local are correct",
          "Run `python -m taskflow init-db` in scanner/ if the tables do not exist yet",
        ]}
      />
    );
  }

  // Rendered once on the server so relative timestamps stay consistent across
  // every card on the page.
  const now = Date.now();

  return (
    <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
      <header className="mb-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h1 className="text-lg font-semibold">Website tasks</h1>
          <p className="muted text-xs">
            {lastScan
              ? lastScan.error
                ? `Last scan failed ${relativeTime(lastScan.started_at, now)}`
                : `Last scan ${relativeTime(lastScan.started_at, now)} · ${lastScan.emails_seen} email${lastScan.emails_seen === 1 ? "" : "s"} read`
              : "No scan has run yet"}
          </p>
        </div>
        <p className="muted mt-1 text-xs">
          Extracted from email, sorted by urgency and impact. Edits you make
          here are never overwritten by a later scan.
        </p>
      </header>

      <div className="mb-4">
        <ScanPanel
          initial={lastScan}
          initialBacklog={measuredScan?.emails_deferred ?? 0}
        />
      </div>

      <div className="mb-5">
        <FilterBar category={params.category} showAll={showAll} />
      </div>

      {tasks.length === 0 ? (
        <SetupNotice
          title={params.category ? "No tasks in this category" : "No tasks yet"}
          detail={
            params.category
              ? "Try another category, or clear the filter."
              : "Run a scan to pull task requests out of your inbox."
          }
          steps={
            params.category
              ? undefined
              : ["cd scanner", "python -m taskflow scan", "Refresh this page"]
          }
        />
      ) : (
        <Board tasks={tasks} now={now} />
      )}
    </main>
  );
}
