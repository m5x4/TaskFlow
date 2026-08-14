"use client";

import Link from "next/link";

import { TASK_DRAG_TYPE } from "@/lib/dnd";
import { relativeTime, senderName, type Task } from "@/lib/types";

import {
  CategoryBadge,
  ImpactBadge,
  LowConfidenceBadge,
  StatusBadge,
  UrgencyBadge,
} from "./Badges";

/*
 * All text here comes from email, which is untrusted. React escapes it by
 * default; nothing in this app uses dangerouslySetInnerHTML, so a task titled
 * `<img onerror=...>` renders as those literal characters.
 *
 * A Client Component only because a card is draggable — the drag handlers are
 * the whole reason. Nothing is fetched here and no state is held; the card is
 * still rendered from server-supplied props.
 */

export function TaskCard({
  task,
  now,
  dragging,
  onDragStart,
  onDragEnd,
  onNudge,
}: {
  task: Task;
  now: number;
  dragging: boolean;
  onDragStart: (id: string) => void;
  onDragEnd: () => void;
  /** Keyboard equivalent of a drop: one column left or right. */
  onNudge: (id: string, delta: -1 | 1) => void;
}) {
  const fileCount = task.task_files?.length ?? 0;

  return (
    <Link
      href={`/task/${task.id}`}
      draggable
      onDragStart={(event) => {
        event.dataTransfer.setData(TASK_DRAG_TYPE, task.id);
        // Anchors put their href on the drag by default, which would drop a URL
        // into any text field it passed over. The title is the honest payload.
        event.dataTransfer.setData("text/plain", task.title);
        event.dataTransfer.effectAllowed = "move";
        onDragStart(task.id);
      }}
      onDragEnd={onDragEnd}
      onKeyDown={(event) => {
        // Shift+arrow is the keyboard route to the same move. Plain arrows
        // scroll the page and Alt/Cmd+arrow is browser history, so neither is
        // available to take.
        if (!event.shiftKey || event.altKey || event.metaKey || event.ctrlKey) {
          return;
        }
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        onNudge(task.id, event.key === "ArrowRight" ? 1 : -1);
      }}
      className={`panel block cursor-grab p-3 transition-shadow
                  hover:shadow-md active:cursor-grabbing
                  focus:ring-2 focus:ring-[#2f6fed]/40 focus:outline-none
                  ${dragging ? "opacity-40" : ""}`}
    >
      <p className="text-sm leading-snug font-medium">{task.title}</p>

      <div className="mt-2 flex flex-wrap gap-1">
        <CategoryBadge value={task.category} />
        <UrgencyBadge value={task.urgency} />
        <ImpactBadge value={task.impact} />
        <LowConfidenceBadge value={task.confidence} />
        {task.status !== "open" && <StatusBadge value={task.status} />}
      </div>

      <div className="muted mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px]">
        {task.emails && (
          <>
            <span className="truncate">{senderName(task.emails.sender)}</span>
            <span aria-hidden>·</span>
          </>
        )}
        <span>{relativeTime(task.created_at, now)}</span>
        {fileCount > 0 && (
          <>
            <span aria-hidden>·</span>
            <span>
              {fileCount} file{fileCount === 1 ? "" : "s"}
            </span>
          </>
        )}
        {task.due_date && (
          <>
            <span aria-hidden>·</span>
            <span>due {task.due_date}</span>
          </>
        )}
      </div>
    </Link>
  );
}
