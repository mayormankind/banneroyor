"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import {
  ACCEPTED_EXTENSIONS,
  ACCEPTED_MEDIA_TYPES,
  STORAGE_BUCKET,
} from "@/lib/constants";
import { formatBytes } from "@/lib/format";

type Phase =
  | { name: "form" }
  | { name: "creating" }
  | { name: "uploading"; progress: number }
  | { name: "finalizing" };

function isAcceptedFile(file: File): boolean {
  const ext = "." + (file.name.split(".").pop() ?? "").toLowerCase();
  return (
    ACCEPTED_EXTENSIONS.includes(ext) ||
    Object.keys(ACCEPTED_MEDIA_TYPES).includes(file.type)
  );
}

/** PUT a file to a Supabase signed upload URL with progress + abort support. */
function uploadWithProgress(
  url: string,
  file: File,
  onProgress: (fraction: number) => void,
): { promise: Promise<void>; abort: () => void } {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<void>((resolve, reject) => {
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new Error(`Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("Upload failed (network error)"));
    xhr.onabort = () => reject(new Error("Upload cancelled"));
    xhr.open("PUT", url);
    xhr.setRequestHeader("content-type", file.type || "application/octet-stream");
    xhr.send(file);
  });
  return { promise, abort: () => xhr.abort() };
}

export function NewMeetingForm({ maxUploadBytes }: { maxUploadBytes: number }) {
  const router = useRouter();
  const abortRef = useRef<(() => void) | null>(null);
  const meetingIdRef = useRef<string | null>(null);

  const [title, setTitle] = useState("");
  const [meetingDate, setMeetingDate] = useState(
    () => new Date().toISOString().slice(0, 10),
  );
  const [description, setDescription] = useState("");
  const [participantNames, setParticipantNames] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [consent, setConsent] = useState(false);
  const [phase, setPhase] = useState<Phase>({ name: "form" });
  const [error, setError] = useState<string | null>(null);

  const fileError = file
    ? !isAcceptedFile(file)
      ? `Unsupported file type. Accepted: ${ACCEPTED_EXTENSIONS.join(", ")}`
      : file.size > maxUploadBytes
        ? `File exceeds the ${formatBytes(maxUploadBytes)} upload limit.`
        : null
    : null;

  function onFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0] ?? null;
    setFile(f);
    setError(null);
  }

  async function cancelUpload() {
    abortRef.current?.();
    abortRef.current = null;
    const meetingId = meetingIdRef.current;
    if (meetingId) {
      const supabase = createClient();
      await supabase.rpc("cancel_meeting_upload", {
        p_meeting_id: meetingId,
      });
    }
    setPhase({ name: "form" });
    setError("Upload cancelled. The meeting was saved as a draft.");
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file || fileError || !consent) return;

    setError(null);
    setPhase({ name: "creating" });
    const supabase = createClient();

    try {
      const {
        data: { user },
      } = await supabase.auth.getUser();
      if (!user) throw new Error("You must be signed in.");

      // 1. Create the meeting record.
      const { data: meeting, error: meetingErr } = await supabase
        .from("meetings")
        .insert({
          owner_id: user.id,
          title: title.trim(),
          description: description.trim() || null,
          meeting_date: new Date(meetingDate).toISOString(),
          recording_consent_confirmed_at: new Date().toISOString(),
        })
        .select("id")
        .single();
      if (meetingErr) throw new Error(meetingErr.message);
      const meetingId = meeting.id as string;
      meetingIdRef.current = meetingId;

      // 2. Record named participants (optional).
      const names = participantNames
        .split(",")
        .map((n) => n.trim())
        .filter(Boolean);
      if (names.length > 0) {
        const { error: pErr } = await supabase.from("participants").insert(
          names.map((n) => ({
            meeting_id: meetingId,
            owner_id: user.id,
            display_name: n,
          })),
        );
        if (pErr) throw new Error(pErr.message);
      }

      // 3. Begin upload (draft -> uploading).
      const { error: beginErr } = await supabase.rpc("begin_meeting_upload", {
        p_meeting_id: meetingId,
      });
      if (beginErr) throw new Error(beginErr.message);

      // 4. Signed upload directly to private storage.
      const safeName = file.name.replace(/[^\w.\-]+/g, "_");
      const path = `${user.id}/${meetingId}/${crypto.randomUUID()}-${safeName}`;
      const { data: signed, error: signErr } = await supabase.storage
        .from(STORAGE_BUCKET)
        .createSignedUploadUrl(path);
      if (signErr) throw new Error(signErr.message);

      setPhase({ name: "uploading", progress: 0 });
      const upload = uploadWithProgress(signed.signedUrl, file, (f) =>
        setPhase({ name: "uploading", progress: f }),
      );
      abortRef.current = upload.abort;
      await upload.promise;
      abortRef.current = null;

      // 5. Finalize + enqueue (atomic, idempotent).
      setPhase({ name: "finalizing" });
      const { error: finErr } = await supabase.rpc("finalize_meeting_upload", {
        p_meeting_id: meetingId,
        p_file_path: path,
        p_file_name: file.name,
        p_mime_type: file.type || "application/octet-stream",
        p_size_bytes: file.size,
      });
      if (finErr) throw new Error(finErr.message);

      router.push(`/meetings/${meetingId}`);
      router.refresh();
    } catch (err) {
      setPhase({ name: "form" });
      setError(
        err instanceof Error ? err.message : "Something went wrong. Try again.",
      );
    }
  }

  const busy = phase.name !== "form";

  return (
    <form
      onSubmit={onSubmit}
      className="rounded-lg border border-border bg-surface p-6 shadow-sm"
    >
      <div className="grid gap-4">
        <label className="block text-sm">
          <span className="mb-1 block font-medium">Title</span>
          <input
            type="text"
            required
            maxLength={300}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            disabled={busy}
            placeholder="Weekly product sync"
            className="w-full rounded-md border border-border bg-background px-3 py-2 outline-none focus:border-accent focus:ring-1 focus:ring-accent"
          />
        </label>

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm">
            <span className="mb-1 block font-medium">Meeting date</span>
            <input
              type="date"
              required
              value={meetingDate}
              onChange={(e) => setMeetingDate(e.target.value)}
              disabled={busy}
              className="w-full rounded-md border border-border bg-background px-3 py-2 outline-none focus:border-accent focus:ring-1 focus:ring-accent"
            />
          </label>
          <label className="block text-sm">
            <span className="mb-1 block font-medium">
              Participants <span className="text-muted">(comma separated)</span>
            </span>
            <input
              type="text"
              value={participantNames}
              onChange={(e) => setParticipantNames(e.target.value)}
              disabled={busy}
              placeholder="Mayowa, Kenny"
              className="w-full rounded-md border border-border bg-background px-3 py-2 outline-none focus:border-accent focus:ring-1 focus:ring-accent"
            />
          </label>
        </div>

        <label className="block text-sm">
          <span className="mb-1 block font-medium">
            Description <span className="text-muted">(optional)</span>
          </span>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={busy}
            rows={2}
            className="w-full rounded-md border border-border bg-background px-3 py-2 outline-none focus:border-accent focus:ring-1 focus:ring-accent"
          />
        </label>

        <div>
          <span className="mb-1 block text-sm font-medium">Recording</span>
          <label
            className={`flex cursor-pointer flex-col items-center justify-center rounded-md border-2 border-dashed px-4 py-8 text-center text-sm ${
              busy
                ? "cursor-not-allowed opacity-60"
                : "border-border hover:border-accent"
            }`}
          >
            <input
              type="file"
              accept={[
                ...Object.keys(ACCEPTED_MEDIA_TYPES),
                ...ACCEPTED_EXTENSIONS,
              ].join(",")}
              onChange={onFileChange}
              disabled={busy}
              className="sr-only"
            />
            {file ? (
              <span>
                <span className="font-medium">{file.name}</span>
                <span className="text-muted"> · {formatBytes(file.size)}</span>
              </span>
            ) : (
              <span className="text-muted">
                Choose an audio or video file (
                {ACCEPTED_EXTENSIONS.join(", ")}) up to{" "}
                {formatBytes(maxUploadBytes)}
              </span>
            )}
          </label>
          {fileError && (
            <p role="alert" className="mt-1 text-sm text-danger">
              {fileError}
            </p>
          )}
        </div>

        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            required
            checked={consent}
            onChange={(e) => setConsent(e.target.checked)}
            disabled={busy}
            className="mt-0.5 accent-teal-700"
          />
          <span>
            I have the right to record and upload this meeting, and I have given
            any legally required notice to — or obtained consent from — the
            people recorded.{" "}
            <span className="text-muted">
              You are responsible for complying with applicable laws and meeting
              policies.
            </span>
          </span>
        </label>

        {phase.name === "uploading" && (
          <div>
            <div
              className="h-2 w-full overflow-hidden rounded-full bg-stone-200"
              role="progressbar"
              aria-valuenow={Math.round(phase.progress * 100)}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className="h-full bg-accent transition-all"
                style={{ width: `${Math.round(phase.progress * 100)}%` }}
              />
            </div>
            <p className="mt-1 text-xs text-muted">
              Uploading… {Math.round(phase.progress * 100)}%
            </p>
          </div>
        )}

        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={busy || !file || !!fileError || !consent}
            className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-strong disabled:opacity-60"
          >
            {phase.name === "creating"
              ? "Creating meeting…"
              : phase.name === "uploading"
                ? "Uploading…"
                : phase.name === "finalizing"
                  ? "Starting processing…"
                  : "Upload and process"}
          </button>
          {phase.name === "uploading" && (
            <button
              type="button"
              onClick={cancelUpload}
              className="rounded-md border border-border px-4 py-2 text-sm hover:bg-stone-50"
            >
              Cancel upload
            </button>
          )}
        </div>
      </div>
    </form>
  );
}
