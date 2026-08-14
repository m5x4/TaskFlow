import type { Category, Impact, Status, Urgency } from "@/lib/types";
import { STATUS_LABELS } from "@/lib/types";

/*
 * Every badge shows a word, not just a color. Color is reinforcement, never
 * the only signal.
 */

const URGENCY_STYLES: Record<Urgency, string> = {
  critical: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  high: "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-300",
  medium: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  low: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
};

const IMPACT_STYLES: Record<Impact, string> = {
  high: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  medium: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  low: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
};

const CATEGORY_STYLES: Record<Category, string> = {
  bug: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300",
  content: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
  feature: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  design: "bg-pink-100 text-pink-800 dark:bg-pink-950 dark:text-pink-300",
  infra: "bg-cyan-100 text-cyan-800 dark:bg-cyan-950 dark:text-cyan-300",
  performance: "bg-teal-100 text-teal-800 dark:bg-teal-950 dark:text-teal-300",
  admin: "bg-stone-100 text-stone-700 dark:bg-stone-800 dark:text-stone-300",
  unclear: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
};

const STATUS_STYLES: Record<Status, string> = {
  open: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  in_progress: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  done: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  wont_do: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-500",
};

export function UrgencyBadge({ value }: { value: Urgency }) {
  return <span className={`chip ${URGENCY_STYLES[value]}`}>{value} urgency</span>;
}

export function ImpactBadge({ value }: { value: Impact }) {
  return <span className={`chip ${IMPACT_STYLES[value]}`}>{value} impact</span>;
}

export function CategoryBadge({ value }: { value: Category }) {
  return <span className={`chip ${CATEGORY_STYLES[value]}`}>{value}</span>;
}

export function StatusBadge({ value }: { value: Status }) {
  return <span className={`chip ${STATUS_STYLES[value]}`}>{STATUS_LABELS[value]}</span>;
}

/** Shown when the model was unsure, so a shaky task is visibly shaky. */
export function LowConfidenceBadge({ value }: { value: number }) {
  if (value >= 0.5) return null;
  return (
    <span
      className="chip bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
      title="The model was not confident this is a real, actionable request"
    >
      unsure ({value.toFixed(2)})
    </span>
  );
}
