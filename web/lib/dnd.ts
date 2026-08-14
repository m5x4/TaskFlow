/**
 * The drag payload for a task card.
 *
 * A private MIME type rather than `text/plain` so the columns can tell one of
 * our cards from anything else the browser lets you drag — a link, a file, a
 * selection. `dataTransfer.getData` is blocked during `dragover` for privacy,
 * but `types` is readable, so this is also what the columns test to decide
 * whether to accept a drop at all.
 */
export const TASK_DRAG_TYPE = "application/x-taskflow-task";

/** True when the drag in flight is one of our cards. */
export function isTaskDrag(transfer: DataTransfer): boolean {
  return Array.from(transfer.types).includes(TASK_DRAG_TYPE);
}
