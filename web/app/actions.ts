"use server";

import { spawn } from "node:child_process";
import path from "node:path";

import { revalidatePath } from "next/cache";

import { fetchLastScan } from "@/lib/queries";
import { getSupabase } from "@/lib/supabase";
import {
  BUCKETS,
  CATEGORIES,
  IMPACTS,
  STATUSES,
  URGENCIES,
  isScanActive,
  priorityForBucket,
  type Bucket,
  type Category,
  type Impact,
  type ScanRun,
  type Status,
  type Urgency,
} from "@/lib/types";

/**
 * Mutations.
 *
 * Server Actions accept whatever the network sends, so every value is checked
 * against its allowed set before it reaches the database rather than trusting
 * the form that produced it.
 *
 * Each write sets manually_edited, which is what stops a later scan from
 * overwriting a human decision (see the ON CONFLICT clause in db.py).
 */

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function requireId(value: FormDataEntryValue | null): string {
  const id = String(value ?? "");
  if (!UUID.test(id)) throw new Error("Invalid task id");
  return id;
}

function requireOneOf<T extends string>(
  value: FormDataEntryValue | null,
  allowed: readonly T[],
  label: string,
): T {
  const candidate = String(value ?? "") as T;
  if (!allowed.includes(candidate)) {
    throw new Error(`Invalid ${label}`);
  }
  return candidate;
}

async function applyUpdate(id: string, patch: Record<string, unknown>) {
  const supabase = getSupabase();
  const { error } = await supabase
    .from("tasks")
    .update({ ...patch, manually_edited: true })
    .eq("id", id);

  if (error) throw new Error(error.message);

  revalidatePath("/");
  revalidatePath(`/task/${id}`);
}

export async function updateStatus(formData: FormData) {
  const id = requireId(formData.get("id"));
  const status = requireOneOf<Status>(formData.get("status"), STATUSES, "status");
  await applyUpdate(id, { status });
}

export async function updatePriority(formData: FormData) {
  const id = requireId(formData.get("id"));
  const urgency = requireOneOf<Urgency>(formData.get("urgency"), URGENCIES, "urgency");
  const impact = requireOneOf<Impact>(formData.get("impact"), IMPACTS, "impact");
  await applyUpdate(id, { urgency, impact });
}

export async function updateCategory(formData: FormData) {
  const id = requireId(formData.get("id"));
  const category = requireOneOf<Category>(
    formData.get("category"),
    CATEGORIES,
    "category",
  );
  await applyUpdate(id, { category });
}

export async function updateNotes(formData: FormData) {
  const id = requireId(formData.get("id"));
  // Bounded so a stray paste cannot write an unbounded blob.
  const notes = String(formData.get("notes") ?? "").slice(0, 5000);
  await applyUpdate(id, { notes });
}

export type MoveResult = { moved: true } | { moved: false; reason: string };

/**
 * Move a task into a priority column — the write behind a card drop.
 *
 * `priority_bucket` is a generated column, so there is nothing to write to it
 * directly: the drop has to be expressed as the urgency/impact pair that
 * generates the target bucket, which is what `priorityForBucket` picks.
 *
 * The current pair is read here rather than sent by the browser. The board is a
 * snapshot — a scan or another tab can have changed the row since it rendered —
 * and preserving the axis the drop does not touch is only meaningful if it
 * preserves the row of record, not what the page happened to show.
 *
 * Unlike the form actions this one returns its failures instead of throwing:
 * the caller is a drag gesture holding an optimistic card, and it needs to be
 * told to put it back.
 */
export async function moveTaskToBucket(
  id: string,
  bucket: Bucket,
): Promise<MoveResult> {
  if (!UUID.test(id)) return { moved: false, reason: "Invalid task id" };
  if (!BUCKETS.some((b) => b.key === bucket)) {
    return { moved: false, reason: "Invalid column" };
  }

  try {
    const supabase = getSupabase();
    const { data, error } = await supabase
      .from("tasks")
      .select("urgency, impact, priority_bucket")
      .eq("id", id)
      .maybeSingle();

    if (error) throw new Error(error.message);
    if (!data) return { moved: false, reason: "That task no longer exists." };

    const current = data as { urgency: Urgency; impact: Impact; priority_bucket: Bucket };

    // Already there — a drop back onto the same column, or a race with another
    // write that got there first. Nothing to do, and no reason to stamp
    // manually_edited for it.
    if (current.priority_bucket === bucket) return { moved: true };

    await applyUpdate(id, priorityForBucket(bucket, current));
    return { moved: true };
  } catch (error) {
    return {
      moved: false,
      reason: error instanceof Error ? error.message : String(error),
    };
  }
}

export async function dismissTask(formData: FormData) {
  const id = requireId(formData.get("id"));
  await applyUpdate(id, { status: "wont_do" });
}

/**
 * Scanning.
 *
 * The scanner is a Python CLI on this machine, so starting it is a request to
 * the operating system rather than a network call — no HTTP service in between.
 * That only holds while the two live together: deployed, this action is the one
 * piece that has to be replaced (see README > Scheduling).
 *
 * Everything else about the feature travels through scan_runs, which is why the
 * progress bar itself would survive that move untouched.
 */

const SCANNER_DIR =
  process.env.SCANNER_PATH ?? path.resolve(process.cwd(), "..", "scanner");

export type ScanTrigger =
  | { started: true }
  | { started: false; reason: string };

export async function startScan(): Promise<ScanTrigger> {
  // Refusing while another scan is in flight is not politeness — two scanners
  // racing can advance the watermark past mail neither has processed, which is
  // the bug that stranded 23 emails in August 2026. launchd already refuses to
  // start a second copy of its own job, but it cannot see a button press.
  const current = await fetchLastScan();
  if (isScanActive(current)) {
    return { started: false, reason: "A scan is already running." };
  }

  const python = path.join(SCANNER_DIR, ".venv", "bin", "python");

  try {
    // Detached with no pipes: a scan runs for minutes and must outlive this
    // request. Nothing reads its output — progress arrives through the database,
    // and the exit status lands in scan_runs.error.
    const child = spawn(python, ["-m", "taskflow", "scan"], {
      cwd: SCANNER_DIR,
      detached: true,
      stdio: "ignore",
      env: { ...process.env, PYTHONUNBUFFERED: "1" },
    });

    child.unref();

    if (child.pid === undefined) {
      return { started: false, reason: "Could not start the scanner process." };
    }
  } catch (error) {
    return {
      started: false,
      reason: error instanceof Error ? error.message : String(error),
    };
  }

  return { started: true };
}

/**
 * Progress for the client to poll.
 *
 * lib/supabase.ts imports `server-only`, so a Client Component cannot query the
 * database directly — polling has to come back through the server. That is the
 * intended shape, not a workaround: the secret key never reaches the browser.
 */
export async function getScanStatus(): Promise<{
  run: ScanRun | null;
  active: boolean;
}> {
  const run = await fetchLastScan();
  return { run, active: isScanActive(run) };
}

/** Pull fresh task rows onto the board once a scan finishes. */
export async function refreshBoard() {
  revalidatePath("/");
}
