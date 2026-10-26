"use client";

import { useRef, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "@/lib/supabase/client";
import { STORAGE_BUCKET } from "@/lib/constants";
import {
  createSpeakerProfile,
  deleteSpeakerProfile,
  setSpeakerReferenceClip,
} from "@/app/(app)/speakers/actions";
import type { SpeakerProfile } from "@/lib/types";

const MAX_CLIP_BYTES = 25 * 1024 * 1024;

export function SpeakerProfilesPanel({
  profiles,
  ownerId,
}: {
  profiles: SpeakerProfile[];
  ownerId: string;
}) {
  const router = useRouter();
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [pending, startTransition] = useTransition();
  const fileRefs = useRef<Record<string, HTMLInputElement | null>>({});

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

  async function uploadClip(profile: SpeakerProfile, file: File) {
    if (file.size > MAX_CLIP_BYTES) {
      throw new Error("Reference clip must be under 25 MB.");
    }
    const safe = file.name.replace(/[^a-zA-Z0-9._-]/g, "_") || "clip";
    const path = `${ownerId}/speaker-refs/${profile.id}/${crypto.randomUUID()}-${safe}`;
    const supabase = createClient();
    const { error: upErr } = await supabase.storage
      .from(STORAGE_BUCKET)
      .upload(path, file, { contentType: file.type || "audio/webm" });
    if (upErr) throw new Error(upErr.message);
    const oldPath = profile.reference_storage_path;
    await setSpeakerReferenceClip(profile.id, path);
    if (oldPath && oldPath !== path) {
      await supabase.storage.from(STORAGE_BUCKET).remove([oldPath]);
    }
  }

  return (
    <div className="grid gap-6">
      <form
        className="flex items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          run(async () => {
            await createSpeakerProfile(name);
            setName("");
            router.refresh();
          });
        }}
      >
        <label className="grid gap-1 text-sm">
          <span className="text-muted">New speaker</span>
          <input
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Mayowa"
            className="rounded-md border border-border bg-background px-3 py-2 text-sm"
          />
        </label>
        <button
          type="submit"
          disabled={pending}
          className="rounded-md bg-accent px-3 py-2 text-sm font-medium text-white hover:bg-accent-strong disabled:opacity-60"
        >
          Add speaker
        </button>
      </form>

      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}

      {profiles.length === 0 ? (
        <p className="rounded-lg border border-dashed border-border bg-surface p-6 text-sm text-muted">
          No known speakers yet. Add the people you meet with regularly and
          upload a short reference clip of their voice — the transcriber will
          use it to identify them automatically in future meetings.
        </p>
      ) : (
        <ul className="grid gap-3">
          {profiles.map((p) => (
            <li
              key={p.id}
              className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface p-4"
            >
              <div className="min-w-0 flex-1">
                <p className="font-medium">{p.display_name}</p>
                <p className="text-xs text-muted">
                  {p.reference_storage_path
                    ? "Voice reference enrolled — used for automatic identification"
                    : "No voice reference — upload a 10–60s clip of them speaking"}
                </p>
              </div>
              <input
                type="file"
                accept="audio/*,video/webm,video/mp4"
                className="hidden"
                ref={(el) => {
                  fileRefs.current[p.id] = el;
                }}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (!file) return;
                  setBusyId(p.id);
                  run(async () => {
                    try {
                      await uploadClip(p, file);
                      router.refresh();
                    } finally {
                      setBusyId(null);
                    }
                  });
                }}
              />
              <button
                onClick={() => fileRefs.current[p.id]?.click()}
                disabled={pending}
                className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-stone-50 disabled:opacity-60"
              >
                {busyId === p.id
                  ? "Uploading…"
                  : p.reference_storage_path
                    ? "Replace clip"
                    : "Upload clip"}
              </button>
              <button
                onClick={() => {
                  if (
                    window.confirm(
                      `Remove ${p.display_name} as a known speaker? Their name stays on past meetings.`,
                    )
                  ) {
                    run(async () => {
                      await deleteSpeakerProfile(p.id);
                      router.refresh();
                    });
                  }
                }}
                disabled={pending}
                className="rounded-md border border-border px-3 py-1.5 text-sm text-danger hover:bg-red-50 disabled:opacity-60"
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      <p className="text-xs text-muted">
        Reference clips are stored privately in your storage and only used to
        identify speakers in your own meetings. Only upload clips of people who
        have consented.
      </p>
    </div>
  );
}
