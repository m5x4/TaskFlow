"use client";

import { startTransition, useOptimistic, useState } from "react";

import { moveTaskToBucket } from "@/app/actions";
import {
  BUCKETS,
  priorityForBucket,
  type Bucket,
  type Task,
} from "@/lib/types";

import { PriorityColumn } from "./PriorityColumn";

/*
 * The board, and the drag gesture that re-prioritises a task.
 *
 * A drop is not a new kind of edit — it is the same urgency/impact write the
 * detail page makes, so it goes through the same Server Action path and picks
 * up manually_edited with it. What the drag adds is that the answer has to
 * appear before the write lands: the card must sit in its new column while the
 * round trip happens, and go back if it fails.
 *
 * That is useOptimistic rather than useState because the correction is not ours
 * to make. The action revalidates `/`, so the server re-renders this page with
 * the real rows; React holds the optimistic bucket until that render arrives
 * and then drops it. Local state would have to guess when to stop, and would
 * flicker the card between the two answers on every move.
 *
 * Native HTML5 drag and drop, no library: it is a whole-card drag between four
 * fixed columns with no reordering inside them, which the platform already
 * does. It does not work on touch, which is why Shift+arrow moves a focused
 * card and the detail page keeps its priority selects.
 */

type Move = { id: string; bucket: Bucket };

export function Board({ tasks, now }: { tasks: Task[]; now: number }) {
  const [optimisticTasks, applyMove] = useOptimistic(
    tasks,
    (state: Task[], move: Move) =>
      state.map((task) =>
        task.id === move.id
          ? {
              ...task,
              priority_bucket: move.bucket,
              // Mirrors what the action will write, so the card's urgency and
              // impact badges do not disagree with the column it just landed in.
              ...priorityForBucket(move.bucket, task),
              manually_edited: true,
            }
          : task,
      ),
  );

  const [draggingId, setDraggingId] = useState<string | null>(null);
  const [overBucket, setOverBucket] = useState<Bucket | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");

  const byBucket = new Map<Bucket, Task[]>(BUCKETS.map((b) => [b.key, []]));
  for (const task of optimisticTasks) {
    byBucket.get(task.priority_bucket)?.push(task);
  }

  function move(id: string, bucket: Bucket) {
    const task = optimisticTasks.find((t) => t.id === id);
    if (!task || task.priority_bucket === bucket) return;

    setError(null);
    setAnnouncement(
      `${task.title} moved to ${BUCKETS.find((b) => b.key === bucket)?.label}`,
    );

    startTransition(async () => {
      // Inside the transition and before the await: this is what React holds
      // on to until the revalidated page replaces it.
      applyMove({ id, bucket });
      const result = await moveTaskToBucket(id, bucket);
      if (!result.moved) {
        setError(result.reason);
        setAnnouncement(`Could not move ${task.title}`);
      }
    });
  }

  function onDrop(bucket: Bucket, id: string) {
    setDraggingId(null);
    setOverBucket(null);
    // The id came off a DataTransfer, so treat it as input: only ids already on
    // this board are acted on. The action validates it again regardless.
    if (!optimisticTasks.some((task) => task.id === id)) return;
    move(id, bucket);
  }

  /** Shift+arrow on a focused card: one column along, in board order. */
  function onNudge(id: string, delta: -1 | 1) {
    const task = optimisticTasks.find((t) => t.id === id);
    if (!task) return;
    const next = BUCKETS[BUCKETS.findIndex((b) => b.key === task.priority_bucket) + delta];
    if (next) move(id, next.key);
  }

  return (
    <div>
      <p className="muted mb-3 text-xs">
        Drag a card to another column to re-prioritise it — or focus one and
        press Shift + ← / →.
      </p>

      {error && (
        <p className="mb-3 text-xs text-[#b91c1c] dark:text-[#f87171]">
          Could not move that task: {error}
        </p>
      )}

      <div
        className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-4"
        onDragEnd={() => {
          // A drag abandoned outside every column still has to clear the state
          // it set — dragend fires on the source whether or not it was dropped.
          setDraggingId(null);
          setOverBucket(null);
        }}
      >
        {BUCKETS.map((bucket) => (
          <PriorityColumn
            key={bucket.key}
            bucket={bucket.key}
            label={bucket.label}
            blurb={bucket.blurb}
            tasks={byBucket.get(bucket.key) ?? []}
            now={now}
            draggingId={draggingId}
            isOver={overBucket === bucket.key}
            onDragStart={setDraggingId}
            onDragEnd={() => {
              setDraggingId(null);
              setOverBucket(null);
            }}
            onDragOver={setOverBucket}
            onDragLeave={(left) =>
              setOverBucket((current) => (current === left ? null : current))
            }
            onDrop={onDrop}
            onNudge={onNudge}
          />
        ))}
      </div>

      {/* Drag has no output a screen reader can follow, so each move says so. */}
      <p aria-live="polite" className="sr-only">
        {announcement}
      </p>
    </div>
  );
}
