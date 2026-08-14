import Link from "next/link";
import { notFound } from "next/navigation";

import {
  CategoryBadge,
  ImpactBadge,
  LowConfidenceBadge,
  StatusBadge,
  UrgencyBadge,
} from "@/components/Badges";
import { SetupNotice } from "@/components/SetupNotice";
import { TaskControls } from "@/components/TaskControls";
import { fetchTask } from "@/lib/queries";
import { isConfigured } from "@/lib/supabase";
import { BUCKETS, gmailUrl, relativeTime } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function TaskPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  if (!isConfigured) {
    return (
      <SetupNotice
        title="Supabase is not configured"
        detail="Set SUPABASE_URL and SUPABASE_SECRET_KEY in web/.env.local."
      />
    );
  }

  const { id } = await params;
  const task = await fetchTask(id);
  if (!task) notFound();

  const email = task.emails;
  const bucket = BUCKETS.find((b) => b.key === task.priority_bucket);
  const now = Date.now();

  return (
    <main className="mx-auto max-w-5xl px-4 py-6 sm:px-6">
      <Link href="/" className="muted text-xs hover:underline">
        ← Back to board
      </Link>

      <header className="mt-3 mb-6">
        <h1 className="text-xl leading-tight font-semibold">{task.title}</h1>
        <div className="mt-2 flex flex-wrap gap-1">
          <CategoryBadge value={task.category} />
          <UrgencyBadge value={task.urgency} />
          <ImpactBadge value={task.impact} />
          <StatusBadge value={task.status} />
          <LowConfidenceBadge value={task.confidence} />
        </div>
        <p className="muted mt-2 text-xs">
          {bucket?.label ?? task.priority_bucket} · created{" "}
          {relativeTime(task.created_at, now)}
          {task.due_date && ` · due ${task.due_date}`}
          {task.manually_edited && " · edited by hand, scans will not overwrite it"}
        </p>
      </header>

      <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_20rem]">
        <div className="flex min-w-0 flex-col gap-5">
          <section className="panel p-4">
            <h2 className="mb-2 text-sm font-semibold">What needs doing</h2>
            {/* Model-written text derived from an untrusted email. React escapes
                it; nothing here renders HTML. */}
            <p className="text-sm leading-relaxed whitespace-pre-wrap">
              {task.description || "No further detail was extracted."}
            </p>
          </section>

          <section className="panel p-4">
            <h2 className="mb-2 text-sm font-semibold">Files to edit</h2>
            {task.task_files?.length ? (
              <ul className="flex flex-col gap-2">
                {task.task_files
                  .slice()
                  .sort((a, b) => b.confidence - a.confidence)
                  .map((file) => (
                    <li
                      key={file.id}
                      className="rounded-lg bg-[#f6f7f9] p-2.5 dark:bg-[#10141a]"
                    >
                      <div className="flex items-baseline justify-between gap-2">
                        <code className="font-mono text-xs break-all">{file.path}</code>
                        <span className="muted shrink-0 text-[11px] tabular-nums">
                          {(file.confidence * 100).toFixed(0)}%
                        </span>
                      </div>
                      <p className="muted mt-1 text-xs">{file.reason}</p>
                    </li>
                  ))}
              </ul>
            ) : (
              <p className="muted text-xs">
                No file suggestions. Set <code>TARGET_REPO_PATH</code> in{" "}
                <code>scanner/.env</code> and run <code>python -m taskflow map</code> to
                fill these in.
              </p>
            )}
          </section>

          {email && (
            <section className="panel p-4">
              <h2 className="mb-2 text-sm font-semibold">Where this came from</h2>
              <dl className="grid grid-cols-[5rem_1fr] gap-x-3 gap-y-1 text-xs">
                <dt className="muted">From</dt>
                <dd className="break-all">{email.sender}</dd>
                <dt className="muted">Subject</dt>
                <dd className="break-words">{email.subject}</dd>
                <dt className="muted">Received</dt>
                <dd>{new Date(email.received_at).toLocaleString()}</dd>
              </dl>
              <p className="muted mt-3 border-l-2 border-[#e3e6ea] pl-3 text-xs
                            leading-relaxed dark:border-[#2c333d]">
                {email.snippet}
              </p>
              <a
                href={gmailUrl(email.gmail_message_id)}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-3 inline-block text-xs text-[#2f6fed] hover:underline"
              >
                Open the original in Gmail ↗
              </a>
            </section>
          )}
        </div>

        <aside className="panel h-fit p-4">
          <TaskControls task={task} />
        </aside>
      </div>
    </main>
  );
}
