import Link from "next/link";
import { notFound } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import { StatusBadge } from "@/components/status-badge";
import { StatusPoller } from "@/components/status-poller";
import { TranscriptViewer } from "@/components/transcript-viewer";
import { ReportSections } from "@/components/report-sections";
import { MeetingHeaderActions } from "@/components/meeting-header-actions";
import { ProcessingPanel } from "@/components/processing-panel";
import { MediaPlayer } from "@/components/media-player";
import { formatDateTime, formatDurationSeconds } from "@/lib/format";
import { ACTIVE_MEETING_STATUSES } from "@/lib/constants";
import type {
  ActionItem,
  Decision,
  KeyPoint,
  Meeting,
  Participant,
  ProcessingJob,
  ProcessingJobEvent,
  Question,
  Summary,
  TranscriptSegment,
} from "@/lib/types";

export const metadata = { title: "Meeting" };

export default async function MeetingReportPage({
  params,
}: PageProps<"/meetings/[id]">) {
  const { id } = await params;
  const supabase = await createClient();

  const { data: meeting } = await supabase
    .from("meetings")
    .select("*")
    .eq("id", id)
    .single();
  if (!meeting) notFound();
  const m = meeting as Meeting;

  const runId = m.current_run_id;

  const [participantsRes, jobRes, eventsRes, segmentsRes, summaryRes, kpRes,
    decRes, actRes, qRes] = await Promise.all([
    supabase
      .from("participants")
      .select("*")
      .eq("meeting_id", id)
      .order("created_at"),
    supabase
      .from("processing_jobs")
      .select("*")
      .eq("meeting_id", id)
      .order("created_at", { ascending: false })
      .limit(1),
    supabase
      .from("processing_job_events")
      .select("*")
      .eq("meeting_id", id)
      .order("created_at")
      .limit(200),
    runId
      ? supabase
          .from("transcript_segments")
          .select("*")
          .eq("meeting_id", id)
          .eq("run_id", runId)
          .order("segment_index")
          .limit(20000)
      : Promise.resolve({ data: [] }),
    runId
      ? supabase
          .from("summaries")
          .select("*")
          .eq("meeting_id", id)
          .eq("run_id", runId)
          .order("created_at", { ascending: false })
          .limit(1)
      : Promise.resolve({ data: [] }),
    runId
      ? supabase
          .from("key_points")
          .select("*")
          .eq("meeting_id", id)
          .eq("run_id", runId)
          .order("sort_order")
      : Promise.resolve({ data: [] }),
    runId
      ? supabase
          .from("decisions")
          .select("*")
          .eq("meeting_id", id)
          .eq("run_id", runId)
          .order("created_at")
      : Promise.resolve({ data: [] }),
    runId
      ? supabase
          .from("action_items")
          .select("*")
          .eq("meeting_id", id)
          .eq("run_id", runId)
          .order("created_at")
      : Promise.resolve({ data: [] }),
    runId
      ? supabase
          .from("questions")
          .select("*")
          .eq("meeting_id", id)
          .eq("run_id", runId)
          .order("created_at")
      : Promise.resolve({ data: [] }),
  ]);

  const participants = (participantsRes.data ?? []) as Participant[];
  const job = ((jobRes.data ?? [])[0] ?? null) as ProcessingJob | null;
  const events = (eventsRes.data ?? []) as ProcessingJobEvent[];
  const segments = (segmentsRes.data ?? []) as TranscriptSegment[];
  const summary = ((summaryRes.data ?? [])[0] ?? null) as Summary | null;
  const keyPoints = (kpRes.data ?? []) as KeyPoint[];
  const decisions = (decRes.data ?? []) as Decision[];
  const actionItems = (actRes.data ?? []) as ActionItem[];
  const questions = (qRes.data ?? []) as Question[];

  const isActive = ACTIVE_MEETING_STATUSES.includes(m.status);
  const hasReport = Boolean(runId && summary);

  return (
    <div className="grid gap-6">
      {isActive && <StatusPoller />}

      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-xl font-semibold tracking-tight">{m.title}</h1>
            <StatusBadge status={m.status} />
          </div>
          <p className="mt-1 text-sm text-muted">
            {formatDateTime(m.meeting_date)} ·{" "}
            {formatDurationSeconds(m.duration_seconds)}
            {m.source_file_name ? ` · ${m.source_file_name}` : ""}
          </p>
          {participants.length > 0 && (
            <p className="mt-0.5 text-sm text-muted">
              {participants.map((p) => p.display_name).join(", ")}
            </p>
          )}
        </div>
        <MeetingHeaderActions meeting={m} canRetry={m.status === "failed"} />
      </header>

      {m.status === "failed" && (
        <div
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-900"
        >
          <p className="font-medium">Processing failed</p>
          <p className="mt-0.5">
            {m.failure_reason ??
              job?.error_message ??
              "An error occurred while processing this meeting."}
          </p>
        </div>
      )}

      {job && m.status !== "completed" && (
        <ProcessingPanel job={job} events={events} />
      )}

      {m.source_file_path && m.status === "completed" && (
        <MediaPlayer meetingId={m.id} />
      )}

      {hasReport ? (
        <ReportSections
          meeting={m}
          summary={summary!}
          keyPoints={keyPoints}
          decisions={decisions}
          actionItems={actionItems}
          questions={questions}
          participants={participants}
        />
      ) : m.status === "completed" ? (
        <p className="rounded-lg border border-border bg-surface p-4 text-sm text-muted">
          Processing completed but no report data was found. Try reprocessing
          the meeting.
        </p>
      ) : null}

      <section aria-labelledby="transcript-heading">
        <h2
          id="transcript-heading"
          className="mb-3 text-base font-semibold tracking-tight"
        >
          Transcript
        </h2>
        {segments.length > 0 ? (
          <TranscriptViewer
            meetingId={m.id}
            segments={segments}
            participants={participants}
          />
        ) : (
          <p className="rounded-lg border border-dashed border-border bg-surface p-6 text-sm text-muted">
            {m.status === "completed"
              ? "No speech was detected in this recording."
              : isActive
                ? "The transcript will appear here once processing finishes."
                : m.status === "failed"
                  ? "Processing failed before a transcript could be produced."
                  : "Upload a recording to generate a transcript."}
          </p>
        )}
      </section>

      <p className="text-sm">
        <Link href="/meetings" className="text-accent hover:underline">
          ← Back to meetings
        </Link>
      </p>
    </div>
  );
}
