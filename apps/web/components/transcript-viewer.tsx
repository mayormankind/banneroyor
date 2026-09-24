"use client";

import { useMemo, useState, useTransition } from "react";
import { formatMs, speakerDisplayName } from "@/lib/format";
import {
  assignSpeaker,
  updateSegmentText,
} from "@/app/(app)/meetings/[id]/actions";
import type { Participant, TranscriptSegment } from "@/lib/types";

interface Props {
  meetingId: string;
  segments: TranscriptSegment[];
  participants: Participant[];
}

export function TranscriptViewer({ meetingId, segments, participants }: Props) {
  const [filter, setFilter] = useState("");
  const [speakerFilter, setSpeakerFilter] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [assigningLabel, setAssigningLabel] = useState<string | null>(null);
  const [assignName, setAssignName] = useState("");
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();

  const participantById = useMemo(
    () => new Map(participants.map((p) => [p.id, p])),
    [participants],
  );
  const participantByLabel = useMemo(
    () =>
      new Map(
        participants
          .filter((p) => p.speaker_label)
          .map((p) => [p.speaker_label as string, p]),
      ),
    [participants],
  );

  const speakerLabels = useMemo(() => {
    const set = new Set<string>();
    segments.forEach((s) => s.speaker_label && set.add(s.speaker_label));
    return [...set].sort();
  }, [segments]);

  const visible = useMemo(() => {
    const f = filter.trim().toLowerCase();
    return segments.filter((s) => {
      if (speakerFilter && s.speaker_label !== speakerFilter) return false;
      if (f && !(s.user_text ?? s.text).toLowerCase().includes(f)) return false;
      return true;
    });
  }, [segments, filter, speakerFilter]);

  function nameFor(s: TranscriptSegment): string {
    const p =
      (s.participant_id && participantById.get(s.participant_id)) ||
      (s.speaker_label ? participantByLabel.get(s.speaker_label) : undefined);
    return speakerDisplayName(s.speaker_label, p?.display_name ?? null);
  }

  async function copyAll() {
    const text = visible
      .map((s) => `[${formatMs(s.start_ms)}] ${nameFor(s)}: ${s.user_text ?? s.text}`)
      .join("\n");
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

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

  return (
    <div className="rounded-lg border border-border bg-surface">
      <div className="flex flex-wrap items-center gap-2 border-b border-border p-3">
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Search transcript…"
          aria-label="Search transcript"
          className="min-w-48 flex-1 rounded-md border border-border bg-background px-3 py-1.5 text-sm outline-none focus:border-accent"
        />
        <select
          value={speakerFilter}
          onChange={(e) => setSpeakerFilter(e.target.value)}
          aria-label="Filter by speaker"
          className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
        >
          <option value="">All speakers</option>
          {speakerLabels.map((l) => (
            <option key={l} value={l}>
              {speakerDisplayName(
                l,
                participantByLabel.get(l)?.display_name ?? null,
              )}
            </option>
          ))}
        </select>
        <button
          onClick={copyAll}
          className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-stone-50"
        >
          {copied ? "Copied" : "Copy transcript"}
        </button>
      </div>

      {error && (
        <p role="alert" className="border-b border-border px-3 py-2 text-sm text-danger">
          {error}
        </p>
      )}

      <div className="max-h-[36rem] overflow-y-auto">
        {visible.length === 0 ? (
          <p className="p-6 text-sm text-muted">
            No transcript segments match the current filters.
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {visible.map((s) => {
              const label = s.speaker_label;
              const mapped = label ? participantByLabel.get(label) : undefined;
              return (
                <li
                  key={s.id}
                  id={`seg-${s.id}`}
                  className="scroll-mt-24 px-4 py-2.5"
                >
                  <div className="flex items-baseline gap-2">
                    <span className="w-14 shrink-0 font-mono text-xs text-muted">
                      {formatMs(s.start_ms)}
                    </span>
                    <span className="text-xs font-medium text-accent">
                      {nameFor(s)}
                    </span>
                    <span className="ml-auto flex gap-1">
                      {label && (
                        <button
                          onClick={() => {
                            setAssigningLabel(label);
                            setAssignName(mapped?.display_name ?? "");
                          }}
                          className="rounded px-1.5 py-0.5 text-xs text-muted hover:bg-stone-100 hover:text-foreground"
                          title="Rename this speaker"
                        >
                          Assign
                        </button>
                      )}
                      <button
                        onClick={() => {
                          setEditingId(s.id);
                          setEditText(s.user_text ?? s.text);
                        }}
                        className="rounded px-1.5 py-0.5 text-xs text-muted hover:bg-stone-100 hover:text-foreground"
                      >
                        Edit
                      </button>
                    </span>
                  </div>

                  {editingId === s.id ? (
                    <form
                      className="ml-14 mt-1"
                      onSubmit={(e) => {
                        e.preventDefault();
                        run(async () => {
                          await updateSegmentText(s.id, meetingId, editText);
                          setEditingId(null);
                        });
                      }}
                    >
                      <textarea
                        value={editText}
                        onChange={(e) => setEditText(e.target.value)}
                        rows={2}
                        className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm"
                      />
                      <div className="mt-1 flex gap-2">
                        <button
                          type="submit"
                          disabled={pending}
                          className="rounded-md bg-accent px-2.5 py-1 text-xs text-white disabled:opacity-60"
                        >
                          Save
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditingId(null)}
                          className="rounded-md border border-border px-2.5 py-1 text-xs"
                        >
                          Cancel
                        </button>
                        {s.user_text && (
                          <button
                            type="button"
                            onClick={() =>
                              run(async () => {
                                await updateSegmentText(s.id, meetingId, "");
                                setEditingId(null);
                              })
                            }
                            className="rounded-md border border-border px-2.5 py-1 text-xs text-muted"
                          >
                            Revert to original
                          </button>
                        )}
                      </div>
                    </form>
                  ) : (
                    <p className="ml-14 mt-0.5 text-sm leading-relaxed">
                      {s.user_text ?? s.text}
                      {s.user_text && (
                        <span
                          className="ml-1 text-xs text-muted"
                          title="Edited by you"
                        >
                          (edited)
                        </span>
                      )}
                    </p>
                  )}

                  {assigningLabel === label && label && (
                    <form
                      className="ml-14 mt-2 flex items-center gap-2 rounded-md bg-stone-50 p-2"
                      onSubmit={(e) => {
                        e.preventDefault();
                        run(async () => {
                          await assignSpeaker(meetingId, label, assignName);
                          setAssigningLabel(null);
                        });
                      }}
                    >
                      <input
                        required
                        value={assignName}
                        onChange={(e) => setAssignName(e.target.value)}
                        placeholder="Who is this speaker?"
                        aria-label="Speaker name"
                        className="rounded-md border border-border bg-background px-2 py-1 text-sm"
                      />
                      <button
                        type="submit"
                        disabled={pending}
                        className="rounded-md bg-accent px-2.5 py-1 text-xs text-white disabled:opacity-60"
                      >
                        Save for all segments
                      </button>
                      <button
                        type="button"
                        onClick={() => setAssigningLabel(null)}
                        className="rounded-md border border-border px-2.5 py-1 text-xs"
                      >
                        Cancel
                      </button>
                    </form>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
      <p className="border-t border-border px-4 py-2 text-xs text-muted">
        {visible.length} of {segments.length} segments
      </p>
    </div>
  );
}
