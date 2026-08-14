import "server-only";

import { getSupabase } from "./supabase";
import type { ScanRun, Status, Task } from "./types";

const TASK_SELECT = `
  id, email_id, title, description, urgency, impact, category,
  priority_bucket, status, due_date, confidence, notes, manually_edited,
  created_at, updated_at,
  emails ( id, gmail_message_id, sender, subject, received_at, snippet ),
  task_files ( id, path, reason, confidence )
`;

export type TaskFilters = {
  statuses?: Status[];
  category?: string;
};

export async function fetchTasks(filters: TaskFilters = {}): Promise<Task[]> {
  const supabase = getSupabase();
  const statuses = filters.statuses ?? ["open", "in_progress"];

  let query = supabase
    .from("tasks")
    .select(TASK_SELECT)
    .in("status", statuses)
    .order("created_at", { ascending: false });

  if (filters.category) {
    query = query.eq("category", filters.category);
  }

  const { data, error } = await query;
  if (error) throw new Error(`Could not load tasks: ${error.message}`);
  return (data ?? []) as unknown as Task[];
}

export async function fetchTask(id: string): Promise<Task | null> {
  const supabase = getSupabase();
  const { data, error } = await supabase
    .from("tasks")
    .select(TASK_SELECT)
    .eq("id", id)
    .maybeSingle();

  if (error) throw new Error(`Could not load task: ${error.message}`);
  return (data as unknown as Task) ?? null;
}

/** Counts per status, for the filter bar. */
export async function fetchStatusCounts(): Promise<Record<string, number>> {
  const supabase = getSupabase();
  const { data, error } = await supabase.from("tasks").select("status");
  if (error) throw new Error(`Could not load counts: ${error.message}`);

  const counts: Record<string, number> = {};
  for (const row of data ?? []) {
    const status = (row as { status: string }).status;
    counts[status] = (counts[status] ?? 0) + 1;
  }
  return counts;
}

const SCAN_SELECT = `
  id, started_at, finished_at, emails_seen, tasks_created, error,
  progress_current, progress_total, progress_subject, heartbeat_at,
  backlog_total, emails_deferred
`;

export async function fetchLastScan(): Promise<ScanRun | null> {
  const supabase = getSupabase();
  const { data, error } = await supabase
    .from("scan_runs")
    .select(SCAN_SELECT)
    .order("started_at", { ascending: false })
    .limit(1)
    .maybeSingle();

  if (error) throw new Error(`Could not load scan history: ${error.message}`);
  return (data as ScanRun) ?? null;
}

/**
 * The newest run that recorded a backlog, for the "not scanned yet" figure.
 *
 * The newest run is not always the right source: a scan that dies before its
 * first email leaves a row with no measurement at all, and reading that would
 * silently report a backlog of zero. Falling back to the last run that actually
 * measured keeps the number stale-but-true instead of confidently wrong.
 */
export async function fetchLastMeasuredScan(): Promise<ScanRun | null> {
  const supabase = getSupabase();
  const { data, error } = await supabase
    .from("scan_runs")
    .select(SCAN_SELECT)
    .not("backlog_total", "is", null)
    .not("finished_at", "is", null)
    .order("started_at", { ascending: false })
    .limit(1)
    .maybeSingle();

  if (error) throw new Error(`Could not load scan history: ${error.message}`);
  return (data as ScanRun) ?? null;
}
