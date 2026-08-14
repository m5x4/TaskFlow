export const URGENCIES = ["critical", "high", "medium", "low"] as const;
export const IMPACTS = ["high", "medium", "low"] as const;
export const STATUSES = ["open", "in_progress", "done", "wont_do"] as const;
export const CATEGORIES = [
  "bug",
  "content",
  "feature",
  "design",
  "infra",
  "performance",
  "admin",
  "unclear",
] as const;

export type Urgency = (typeof URGENCIES)[number];
export type Impact = (typeof IMPACTS)[number];
export type Status = (typeof STATUSES)[number];
export type Category = (typeof CATEGORIES)[number];
export type Bucket = "do_now" | "quick_win" | "schedule" | "backlog";

/** Display order and labels. Bucket order matches the SQL sort in db.py. */
export const BUCKETS: { key: Bucket; label: string; blurb: string }[] = [
  { key: "do_now", label: "Do now", blurb: "Urgent and high impact" },
  { key: "quick_win", label: "Quick wins", blurb: "Urgent, lower impact" },
  { key: "schedule", label: "Schedule", blurb: "High impact, not urgent" },
  { key: "backlog", label: "Backlog", blurb: "Neither urgent nor high impact" },
];

/**
 * The urgency/impact pair that lands a task in a given bucket.
 *
 * The inverse of the `priority_bucket` generated column in
 * `migrations/001_init.sql` (mirrored in `taskflow/models.py`) — change the
 * bucket rules there and this has to change with them.
 *
 * Twelve pairs collapse into four buckets, so the inverse is a choice rather
 * than a lookup. Whatever the task already says is kept whenever it is
 * compatible with the target, and only the axis that disagrees moves: dragging
 * a critical/low task into "Do now" raises its impact and leaves it critical,
 * instead of flattening every dropped card to high/high.
 */
export function priorityForBucket(
  bucket: Bucket,
  current: { urgency: Urgency; impact: Impact },
): { urgency: Urgency; impact: Impact } {
  const wantsUrgent = bucket === "do_now" || bucket === "quick_win";
  const wantsHighImpact = bucket === "do_now" || bucket === "schedule";
  const isUrgent = current.urgency === "critical" || current.urgency === "high";

  return {
    urgency: wantsUrgent
      ? isUrgent
        ? current.urgency
        : "high"
      : isUrgent
        ? "medium"
        : current.urgency,
    impact: wantsHighImpact
      ? "high"
      : current.impact === "high"
        ? "medium"
        : current.impact,
  };
}

export const STATUS_LABELS: Record<Status, string> = {
  open: "Open",
  in_progress: "In progress",
  done: "Done",
  wont_do: "Dismissed",
};

export type TaskFile = {
  id: string;
  path: string;
  reason: string;
  confidence: number;
};

export type SourceEmail = {
  id: string;
  gmail_message_id: string;
  sender: string;
  subject: string;
  received_at: string;
  snippet: string;
};

export type Task = {
  id: string;
  email_id: string;
  title: string;
  description: string;
  urgency: Urgency;
  impact: Impact;
  category: Category;
  priority_bucket: Bucket;
  status: Status;
  due_date: string | null;
  confidence: number;
  notes: string;
  manually_edited: boolean;
  created_at: string;
  updated_at: string;
  emails: SourceEmail | null;
  task_files: TaskFile[];
};

export type ScanRun = {
  id: string;
  started_at: string;
  finished_at: string | null;
  emails_seen: number;
  tasks_created: number;
  error: string | null;
  /** Position within the todo list, not the Gmail window. */
  progress_current: number;
  progress_total: number;
  progress_subject: string | null;
  heartbeat_at: string | null;
  /** Everything matching the Gmail query when the scan started. */
  backlog_total: number | null;
  /** The part of that window the run could not reach — the unscanned count. */
  emails_deferred: number;
};

/**
 * A run whose heartbeat has gone quiet was killed, not finished.
 *
 * Without this the "already scanning" lock would latch forever the first time a
 * scan is interrupted — which has already happened once in this project's
 * history. The scanner beats before every email, and pacing at 12/min means a
 * healthy gap is about five seconds, so two minutes is a wide margin.
 */
export const SCAN_STALE_MS = 2 * 60 * 1000;

export function isScanActive(run: ScanRun | null, now = Date.now()): boolean {
  if (!run || run.finished_at) return false;
  const beat = Date.parse(run.heartbeat_at ?? run.started_at);
  return Number.isFinite(beat) && now - beat < SCAN_STALE_MS;
}

/** Gmail deep link for a message id. */
export function gmailUrl(messageId: string): string {
  return `https://mail.google.com/mail/u/0/#all/${messageId}`;
}

/** "3 days ago" style relative time, computed on the server to avoid hydration drift. */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const seconds = Math.round((now - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.round(days / 30);
  return `${months}mo ago`;
}

/** Sender display name, falling back to the bare address. */
export function senderName(sender: string): string {
  const match = sender.match(/^\s*"?([^"<]+?)"?\s*</);
  if (match) return match[1].trim();
  return sender.replace(/[<>]/g, "").trim();
}
