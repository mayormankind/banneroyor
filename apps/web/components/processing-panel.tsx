import { STAGE_LABEL } from "@/lib/constants";
import type { ProcessingJob, ProcessingJobEvent } from "@/lib/types";

const EVENT_STYLE: Record<string, string> = {
  started: "text-muted",
  completed: "text-teal-700",
  failed: "text-red-700",
  retrying: "text-amber-700",
  skipped: "text-muted",
};

function fmtDuration(ms: number | null): string {
  if (ms == null) return "";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

export function ProcessingPanel({
  job,
  events,
}: {
  job: ProcessingJob;
  events: ProcessingJobEvent[];
}) {
  return (
    <section
      aria-labelledby="processing-heading"
      className="rounded-lg border border-border bg-surface p-4"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="processing-heading" className="text-sm font-semibold">
          Processing
        </h2>
        <span className="text-xs text-muted">
          Job status: {job.status}
          {job.current_stage
            ? ` · ${STAGE_LABEL[job.current_stage] ?? job.current_stage}`
            : ""}
          {job.attempts > 0 ? ` · attempt ${job.attempts}/${job.max_attempts}` : ""}
        </span>
      </div>

      {job.status === "failed" && job.error_message && (
        <p className="mt-2 text-sm text-danger">{job.error_message}</p>
      )}

      {events.length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs text-muted hover:text-foreground">
            Stage history ({events.length})
          </summary>
          <ul className="mt-2 space-y-1 text-xs">
            {events.map((e) => (
              <li key={e.id} className="flex flex-wrap gap-2">
                <span className="w-36 text-muted">
                  {STAGE_LABEL[e.stage] ?? e.stage}
                </span>
                <span className={EVENT_STYLE[e.status] ?? ""}>{e.status}</span>
                <span className="text-muted">
                  {fmtDuration(e.duration_ms)}
                  {e.attempt_number > 1 ? ` · try ${e.attempt_number}` : ""}
                  {e.error_message ? ` — ${e.error_message}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
