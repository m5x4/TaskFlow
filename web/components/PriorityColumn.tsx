"use client";

import { isTaskDrag, TASK_DRAG_TYPE } from "@/lib/dnd";
import type { Bucket, Task } from "@/lib/types";

import { TaskCard } from "./TaskCard";

export function PriorityColumn({
  bucket,
  label,
  blurb,
  tasks,
  now,
  draggingId,
  isOver,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDragLeave,
  onDrop,
  onNudge,
}: {
  bucket: Bucket;
  label: string;
  blurb: string;
  tasks: Task[];
  now: number;
  draggingId: string | null;
  isOver: boolean;
  onDragStart: (id: string) => void;
  onDragEnd: () => void;
  onDragOver: (bucket: Bucket) => void;
  onDragLeave: (bucket: Bucket) => void;
  onDrop: (bucket: Bucket, id: string) => void;
  onNudge: (id: string, delta: -1 | 1) => void;
}) {
  return (
    <section className="flex min-w-0 flex-col gap-3">
      <header className="flex items-baseline justify-between gap-2 border-b
                         border-[#e3e6ea] pb-2 dark:border-[#262c36]">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">{label}</h2>
          <p className="muted text-[11px]">{blurb}</p>
        </div>
        <span className="muted shrink-0 text-xs tabular-nums">{tasks.length}</span>
      </header>

      {/*
       * The drop target is the whole list, not each card, so a column that is
       * empty or short still catches a drop — hence the min height. Only drags
       * carrying our own MIME type get preventDefault, which is what makes a
       * dragged link or file bounce off instead of landing here.
       */}
      <div
        onDragOver={(event) => {
          if (!isTaskDrag(event.dataTransfer)) return;
          event.preventDefault();
          event.dataTransfer.dropEffect = "move";
          if (!isOver) onDragOver(bucket);
        }}
        onDragLeave={(event) => {
          // dragleave also fires when the pointer crosses onto a child, which
          // would flicker the highlight off over every card in the column.
          if (event.currentTarget.contains(event.relatedTarget as Node | null)) {
            return;
          }
          onDragLeave(bucket);
        }}
        onDrop={(event) => {
          if (!isTaskDrag(event.dataTransfer)) return;
          event.preventDefault();
          onDrop(bucket, event.dataTransfer.getData(TASK_DRAG_TYPE));
        }}
        className={`flex min-h-24 flex-col gap-2 rounded-lg transition-colors
                    ${
                      isOver
                        ? "bg-[#2f6fed]/8 outline-2 outline-dashed outline-[#2f6fed]/70"
                        : "outline-2 outline-transparent"
                    }`}
      >
        {tasks.length === 0 ? (
          <p className="muted rounded-lg border border-dashed border-[#e3e6ea]
                        px-3 py-6 text-center text-xs dark:border-[#262c36]">
            {isOver ? "Drop here" : "Nothing here"}
          </p>
        ) : (
          tasks.map((task) => (
            <TaskCard
              key={task.id}
              task={task}
              now={now}
              dragging={draggingId === task.id}
              onDragStart={onDragStart}
              onDragEnd={onDragEnd}
              onNudge={onNudge}
            />
          ))
        )}
      </div>
    </section>
  );
}
