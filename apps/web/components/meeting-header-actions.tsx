"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import {
  deleteMeeting,
  requestMeetingRetry,
  updateMeetingDetails,
} from "@/app/(app)/meetings/[id]/actions";
import type { Meeting } from "@/lib/types";

export function MeetingHeaderActions({
  meeting,
  canRetry,
}: {
  meeting: Meeting;
  canRetry: boolean;
}) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const [title, setTitle] = useState(meeting.title);
  const [description, setDescription] = useState(meeting.description ?? "");
  const [date, setDate] = useState(meeting.meeting_date.slice(0, 10));

  function run(fn: () => Promise<void>) {
    setError(null);
    startTransition(async () => {
      try {
        await fn();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Action failed");
      }
    });
  }

  if (editing) {
    return (
      <form
        className="w-full rounded-lg border border-border bg-surface p-4 sm:w-96"
        onSubmit={(e) => {
          e.preventDefault();
          run(async () => {
            await updateMeetingDetails(meeting.id, {
              title,
              description: description.trim() || null,
              meeting_date: date,
            });
            setEditing(false);
          });
        }}
      >
        <label className="mb-2 block text-sm">
          <span className="mb-1 block text-xs text-muted">Title</span>
          <input
            required
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          />
        </label>
        <label className="mb-2 block text-sm">
          <span className="mb-1 block text-xs text-muted">Date</span>
          <input
            type="date"
            required
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          />
        </label>
        <label className="mb-3 block text-sm">
          <span className="mb-1 block text-xs text-muted">Description</span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
          />
        </label>
        {error && <p className="mb-2 text-sm text-danger">{error}</p>}
        <div className="flex gap-2">
          <button
            type="submit"
            disabled={pending}
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white disabled:opacity-60"
          >
            Save
          </button>
          <button
            type="button"
            onClick={() => setEditing(false)}
            className="rounded-md border border-border px-3 py-1.5 text-sm"
          >
            Cancel
          </button>
        </div>
      </form>
    );
  }

  return (
    <div className="flex flex-col items-end gap-2">
      <div className="flex gap-2">
        <button
          onClick={() => setEditing(true)}
          className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-stone-50"
        >
          Edit
        </button>
        {canRetry && (
          <button
            onClick={() =>
              run(async () => {
                await requestMeetingRetry(meeting.id);
                router.refresh();
              })
            }
            disabled={pending}
            className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-stone-50 disabled:opacity-60"
          >
            Retry processing
          </button>
        )}
        <button
          onClick={() => setConfirmingDelete(true)}
          className="rounded-md border border-red-200 px-3 py-1.5 text-sm text-danger hover:bg-red-50"
        >
          Delete
        </button>
      </div>
      {error && <p className="text-sm text-danger">{error}</p>}
      {confirmingDelete && (
        <div
          role="alertdialog"
          aria-label="Delete meeting"
          className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm"
        >
          <p className="mb-2 text-red-900">
            Delete this meeting, its recording, transcript, and report? This
            cannot be undone.
          </p>
          <div className="flex gap-2">
            <button
              onClick={() =>
                run(() => deleteMeeting(meeting.id))
              }
              disabled={pending}
              className="rounded-md bg-danger px-3 py-1.5 text-white disabled:opacity-60"
            >
              {pending ? "Deleting…" : "Delete permanently"}
            </button>
            <button
              onClick={() => setConfirmingDelete(false)}
              className="rounded-md border border-border bg-surface px-3 py-1.5"
            >
              Keep
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
