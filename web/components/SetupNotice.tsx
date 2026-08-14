/**
 * Shown instead of a stack trace when the app has not been wired up yet, or
 * when the database is unreachable. A first-run screen is the most likely
 * screen a new install will show, so it should explain the next step.
 */
export function SetupNotice({
  title,
  detail,
  steps,
}: {
  title: string;
  detail?: string;
  steps?: string[];
}) {
  return (
    <div className="panel mx-auto mt-16 max-w-xl p-6">
      <h2 className="text-base font-semibold">{title}</h2>
      {detail && <p className="muted mt-2 text-sm">{detail}</p>}
      {steps && steps.length > 0 && (
        <ol className="mt-4 space-y-2 text-sm">
          {steps.map((step, i) => (
            <li key={i} className="flex gap-2">
              <span className="muted shrink-0 tabular-nums">{i + 1}.</span>
              <span>{step}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
