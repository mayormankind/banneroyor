"use client";

import { useState, useTransition } from "react";
import {
  updateActionItem,
  updateActionItemStatus,
  updateDecision,
  updateKeyPoint,
  updateQuestion,
  updateSummary,
} from "@/app/(app)/meetings/[id]/actions";
import { formatMs } from "@/lib/format";
import type {
  ActionItem,
  Decision,
  KeyPoint,
  Meeting,
  Participant,
  Question,
  Summary,
} from "@/lib/types";

interface Props {
  meeting: Meeting;
  summary: Summary;
  keyPoints: KeyPoint[];
  decisions: Decision[];
  actionItems: ActionItem[];
  questions: Question[];
  participants: Participant[];
}

function EvidenceLink({
  segmentIds,
  timestampMs,
}: {
  segmentIds: string[];
  timestampMs: number | null;
}) {
  const target = segmentIds[0];
  if (!target && timestampMs == null) return null;
  const label = timestampMs != null ? `@ ${formatMs(timestampMs)}` : "source";
  return target ? (
    <a
      href={`#seg-${target}`}
      className="ml-2 whitespace-nowrap text-xs text-accent hover:underline"
    >
      {label}
    </a>
  ) : (
    <span className="ml-2 whitespace-nowrap text-xs text-muted">{label}</span>
  );
}

function EditableText({
  text,
  isEdited,
  onSave,
  multiline = true,
}: {
  text: string;
  isEdited: boolean;
  onSave: (next: string) => Promise<void>;
  multiline?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(text);
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);

  if (!editing) {
    return (
      <span className="group inline">
        {text}
        {isEdited && (
          <span className="ml-1 text-xs text-muted">(edited)</span>
        )}
        <button
          onClick={() => {
            setDraft(text);
            setEditing(true);
          }}
          aria-label="Edit"
          className="ml-2 rounded px-1 text-xs text-muted opacity-0 group-hover:opacity-100 hover:text-foreground focus:opacity-100"
        >
          Edit
        </button>
      </span>
    );
  }

  return (
    <span className="block">
      {multiline ? (
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          rows={3}
          className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
        />
      ) : (
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
        />
      )}
      {error && <span className="block text-xs text-danger">{error}</span>}
      <span className="mt-1 flex gap-2">
        <button
          disabled={pending}
          onClick={() =>
            startTransition(async () => {
              try {
                await onSave(draft.trim());
                setEditing(false);
              } catch (e) {
                setError(e instanceof Error ? e.message : "Save failed");
              }
            })
          }
          className="rounded-md bg-accent px-2.5 py-1 text-xs text-white disabled:opacity-60"
        >
          Save
        </button>
        <button
          onClick={() => setEditing(false)}
          className="rounded-md border border-border px-2.5 py-1 text-xs"
        >
          Cancel
        </button>
      </span>
    </span>
  );
}

const ACTION_STATUS_LABEL: Record<string, string> = {
  open: "Open",
  in_progress: "In progress",
  completed: "Done",
  cancelled: "Cancelled",
};

export function ReportSections({
  meeting,
  summary,
  keyPoints,
  decisions,
  actionItems,
  questions,
  participants,
}: Props) {
  const participantName = (id: string | null) =>
    id ? participants.find((p) => p.id === id)?.display_name : undefined;

  return (
    <div className="grid gap-6">
      <section aria-labelledby="summary-heading">
        <h2 id="summary-heading" className="mb-2 text-base font-semibold">
          Summary
        </h2>
        <div className="rounded-lg border border-border bg-surface p-4 text-sm leading-relaxed">
          <EditableText
            text={summary.summary}
            isEdited={summary.is_edited}
            onSave={(t) => updateSummary(summary.id, meeting.id, t)}
          />
        </div>
      </section>

      {keyPoints.length > 0 && (
        <section aria-labelledby="kp-heading">
          <h2 id="kp-heading" className="mb-2 text-base font-semibold">
            Key points
          </h2>
          <ul className="space-y-1.5 rounded-lg border border-border bg-surface p-4 text-sm">
            {keyPoints.map((k) => (
              <li key={k.id} className="flex items-baseline">
                <span className="mr-2 text-muted">•</span>
                <span className="flex-1">
                  <EditableText
                    text={k.content}
                    isEdited={k.is_edited}
                    onSave={(t) => updateKeyPoint(k.id, meeting.id, t)}
                  />
                </span>
                <EvidenceLink
                  segmentIds={k.evidence_segment_ids}
                  timestampMs={k.timestamp_ms}
                />
              </li>
            ))}
          </ul>
        </section>
      )}

      {decisions.length > 0 && (
        <section aria-labelledby="decisions-heading">
          <h2 id="decisions-heading" className="mb-2 text-base font-semibold">
            Decisions
          </h2>
          <ul className="space-y-1.5 rounded-lg border border-border bg-surface p-4 text-sm">
            {decisions.map((d) => (
              <li key={d.id} className="flex items-baseline">
                <span className="mr-2 text-muted">•</span>
                <span className="flex-1">
                  <EditableText
                    text={d.description}
                    isEdited={d.is_edited}
                    onSave={(t) => updateDecision(d.id, meeting.id, t)}
                  />
                  {participantName(d.participant_id) && (
                    <span className="ml-1 text-xs text-muted">
                      — {participantName(d.participant_id)}
                    </span>
                  )}
                </span>
                <EvidenceLink
                  segmentIds={d.evidence_segment_ids}
                  timestampMs={d.timestamp_ms}
                />
              </li>
            ))}
          </ul>
        </section>
      )}

      <section aria-labelledby="actions-heading">
        <h2 id="actions-heading" className="mb-2 text-base font-semibold">
          Action items
        </h2>
        {actionItems.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border bg-surface p-4 text-sm text-muted">
            No action items were identified in this meeting.
          </p>
        ) : (
          <ul className="space-y-2 rounded-lg border border-border bg-surface p-4 text-sm">
            {actionItems.map((a) => (
              <ActionItemRow
                key={a.id}
                item={a}
                meetingId={meeting.id}
                ownerName={participantName(a.owner_participant_id)}
                participants={participants}
              />
            ))}
          </ul>
        )}
      </section>

      {questions.length > 0 && (
        <section aria-labelledby="questions-heading">
          <h2 id="questions-heading" className="mb-2 text-base font-semibold">
            Open questions
          </h2>
          <ul className="space-y-1.5 rounded-lg border border-border bg-surface p-4 text-sm">
            {questions.map((q) => (
              <li key={q.id} className="flex items-baseline">
                <input
                  type="checkbox"
                  checked={q.resolved}
                  onChange={() =>
                    updateQuestion(q.id, meeting.id, { resolved: !q.resolved })
                  }
                  aria-label={`Mark "${q.question}" as ${
                    q.resolved ? "unresolved" : "resolved"
                  }`}
                  className="mr-2 mt-0.5 shrink-0 self-start accent-teal-700"
                />
                <span
                  className={`flex-1 ${q.resolved ? "text-muted line-through" : ""}`}
                >
                  <EditableText
                    text={q.question}
                    isEdited={q.is_edited}
                    onSave={(t) =>
                      updateQuestion(q.id, meeting.id, { question: t })
                    }
                  />
                </span>
                <EvidenceLink
                  segmentIds={q.evidence_segment_ids}
                  timestampMs={q.timestamp_ms}
                />
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function ActionItemRow({
  item,
  meetingId,
  ownerName,
  participants,
}: {
  item: ActionItem;
  meetingId: string;
  ownerName: string | undefined;
  participants: Participant[];
}) {
  const [editing, setEditing] = useState(false);
  const [description, setDescription] = useState(item.description);
  const [dueDate, setDueDate] = useState(item.due_date ?? "");
  const [ownerId, setOwnerId] = useState(item.owner_participant_id ?? "");
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  return (
    <li className="flex items-start gap-3">
      <select
        value={item.status}
        onChange={(e) =>
          updateActionItemStatus(
            item.id,
            meetingId,
            e.target.value as ActionItem["status"],
          )
        }
        aria-label="Action item status"
        className={`mt-0.5 rounded-md border border-border px-1.5 py-1 text-xs ${
          item.status === "completed" ? "text-muted" : ""
        }`}
      >
        {Object.entries(ACTION_STATUS_LABEL).map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>

      <div className="flex-1">
        {editing ? (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              startTransition(async () => {
                try {
                  await updateActionItem(item.id, meetingId, {
                    description: description.trim(),
                    due_date: dueDate || null,
                    owner_participant_id: ownerId || null,
                  });
                  setEditing(false);
                } catch (err) {
                  setError(
                    err instanceof Error ? err.message : "Save failed",
                  );
                }
              });
            }}
          >
            <input
              required
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
            />
            <div className="mt-1.5 flex flex-wrap gap-2">
              <input
                type="date"
                value={dueDate}
                onChange={(e) => setDueDate(e.target.value)}
                aria-label="Due date"
                className="rounded-md border border-border bg-background px-2 py-1 text-xs"
              />
              <select
                value={ownerId}
                onChange={(e) => setOwnerId(e.target.value)}
                aria-label="Owner"
                className="rounded-md border border-border bg-background px-2 py-1 text-xs"
              >
                <option value="">Unassigned</option>
                {participants.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.display_name}
                  </option>
                ))}
              </select>
              <button
                type="submit"
                disabled={pending}
                className="rounded-md bg-accent px-2.5 py-1 text-xs text-white disabled:opacity-60"
              >
                Save
              </button>
              <button
                type="button"
                onClick={() => setEditing(false)}
                className="rounded-md border border-border px-2.5 py-1 text-xs"
              >
                Cancel
              </button>
            </div>
            {error && <p className="mt-1 text-xs text-danger">{error}</p>}
          </form>
        ) : (
          <span className="group">
            <span className={item.status === "completed" ? "line-through" : ""}>
              {item.description}
            </span>
            {item.is_edited && (
              <span className="ml-1 text-xs text-muted">(edited)</span>
            )}
            <button
              onClick={() => {
                setDescription(item.description);
                setDueDate(item.due_date ?? "");
                setOwnerId(item.owner_participant_id ?? "");
                setEditing(true);
              }}
              aria-label="Edit action item"
              className="ml-2 rounded px-1 text-xs text-muted opacity-0 group-hover:opacity-100 hover:text-foreground focus:opacity-100"
            >
              Edit
            </button>
            <span className="ml-2 text-xs text-muted">
              {ownerName ? `@${ownerName}` : ""}
              {item.due_date ? ` · due ${item.due_date}` : ""}
            </span>
            <EvidenceLink
              segmentIds={item.evidence_segment_ids}
              timestampMs={item.timestamp_ms}
            />
          </span>
        )}
      </div>
    </li>
  );
}
