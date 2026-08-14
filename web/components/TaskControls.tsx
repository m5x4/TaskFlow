import {
  dismissTask,
  updateCategory,
  updateNotes,
  updatePriority,
  updateStatus,
} from "@/app/actions";
import {
  CATEGORIES,
  IMPACTS,
  STATUSES,
  STATUS_LABELS,
  URGENCIES,
  type Task,
} from "@/lib/types";

/*
 * Plain forms posting to Server Actions. No client-side state, no data
 * fetching in the browser, and no Supabase key anywhere near it — each form
 * submits, the action validates and writes, and the page revalidates.
 */

function Label({ children }: { children: React.ReactNode }) {
  return (
    <span className="muted mb-1 block text-[11px] font-medium tracking-wide uppercase">
      {children}
    </span>
  );
}

export function TaskControls({ task }: { task: Task }) {
  return (
    <div className="flex flex-col gap-5">
      <form action={updateStatus} className="flex flex-col">
        <input type="hidden" name="id" value={task.id} />
        <Label>Status</Label>
        <div className="flex gap-2">
          <select name="status" defaultValue={task.status} className="field flex-1">
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABELS[s]}
              </option>
            ))}
          </select>
          <button type="submit" className="btn-primary">
            Set
          </button>
        </div>
      </form>

      <form action={updatePriority} className="flex flex-col">
        <input type="hidden" name="id" value={task.id} />
        <Label>Priority</Label>
        {/* Two selects plus a button do not fit side by side in the sidebar,
            so the button drops to its own line. */}
        <div className="flex gap-2">
          <select
            name="urgency"
            defaultValue={task.urgency}
            className="field w-0 flex-1"
          >
            {URGENCIES.map((u) => (
              <option key={u} value={u}>
                {u} urgency
              </option>
            ))}
          </select>
          <select
            name="impact"
            defaultValue={task.impact}
            className="field w-0 flex-1"
          >
            {IMPACTS.map((i) => (
              <option key={i} value={i}>
                {i} impact
              </option>
            ))}
          </select>
        </div>
        <p className="muted mt-1 text-[11px]">
          Changing either one moves this task between columns.
        </p>
        <button type="submit" className="btn mt-2 self-start">
          Save priority
        </button>
      </form>

      <form action={updateCategory} className="flex flex-col">
        <input type="hidden" name="id" value={task.id} />
        <Label>Category</Label>
        <div className="flex gap-2">
          <select name="category" defaultValue={task.category} className="field flex-1">
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <button type="submit" className="btn">
            Save
          </button>
        </div>
      </form>

      <form action={updateNotes} className="flex flex-col">
        <input type="hidden" name="id" value={task.id} />
        <Label>Notes</Label>
        <textarea
          name="notes"
          rows={4}
          defaultValue={task.notes}
          maxLength={5000}
          placeholder="Anything the email did not say — what you tried, what it depends on."
          className="field resize-y"
        />
        <button type="submit" className="btn mt-2 self-start">
          Save notes
        </button>
      </form>

      {task.status !== "wont_do" && (
        <form action={dismissTask} className="border-t border-[#e3e6ea] pt-4
                                              dark:border-[#262c36]">
          <input type="hidden" name="id" value={task.id} />
          <button
            type="submit"
            className="text-xs text-red-700 hover:underline dark:text-red-400"
          >
            Dismiss — this was not a real task
          </button>
          <p className="muted mt-1 text-[11px]">
            Keeps the row for the record, hidden from the board.
          </p>
        </form>
      )}
    </div>
  );
}
