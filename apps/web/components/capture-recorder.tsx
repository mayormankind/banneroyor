"use client";

import { useEffect, useRef, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { BrowserAudioCapture } from "@/lib/capture/recorder";
import { ingestRecording } from "@/lib/capture/ingest";
import { formatBytes, formatMs } from "@/lib/format";
import { STORAGE_BUCKET } from "@/lib/constants";
import { LiveTranscriptPanel } from "@/components/live-transcript";
import type { CaptureSessionStatus } from "@/lib/capture/types";

/** Live part cadence — each timeslice becomes one uploaded part + sync job. */
const LIVE_PART_MS = 20_000;

type CapturePhase =
  | "idle"
  | "preparing"
  | "requesting_permission"
  | "capturing"
  | "paused"
  | "uploading"
  | "finalizing";

export interface PreparedMeeting {
  meetingId: string;
  sessionId: string;
  ownerId: string;
}

/**
 * In-browser capture panel. Owns the capture session lifecycle; the parent
 * supplies meeting/session creation (onPrepare) and completion navigation.
 */
export function CaptureRecorder({
  maxUploadBytes,
  onPrepare,
  onComplete,
  onError,
  onBusyChange,
}: {
  maxUploadBytes: number;
  onPrepare: () => Promise<PreparedMeeting>;
  onComplete: (meetingId: string) => void;
  onError: (message: string) => void;
  onBusyChange?: (busy: boolean) => void;
}) {
  const captureRef = useRef<BrowserAudioCapture | null>(null);
  const [phase, setPhase] = useState<CapturePhase>("idle");
  const [elapsed, setElapsed] = useState(0);
  const [systemAudio, setSystemAudio] = useState(false);
  const [systemAudioActive, setSystemAudioActive] = useState(false);
  const [progress, setProgress] = useState(0);
  const [partFailures, setPartFailures] = useState(0);
  const [liveIds, setLiveIds] = useState<{
    meetingId: string;
    sessionId: string;
  } | null>(null);
  const meetingRef = useRef<PreparedMeeting | null>(null);
  const partQueueRef = useRef<Promise<void>>(Promise.resolve());

  const supported = BrowserAudioCapture.isSupported();
  const canShareSystem = BrowserAudioCapture.canShareSystemAudio();

  useEffect(() => {
    if (phase !== "capturing" && phase !== "paused") return;
    const id = setInterval(
      () => setElapsed(captureRef.current?.elapsedSeconds() ?? 0),
      250,
    );
    return () => clearInterval(id);
  }, [phase]);

  async function setSessionStatus(
    sessionId: string,
    status: CaptureSessionStatus,
    extra: Record<string, unknown> = {},
  ) {
    const supabase = createClient();
    await supabase
      .from("capture_sessions")
      .update({ status, ...extra })
      .eq("id", sessionId);
  }

  async function failSession(message: string) {
    const m = meetingRef.current;
    if (m) {
      await setSessionStatus(m.sessionId, "failed", {
        error_message: message,
        ended_at: new Date().toISOString(),
      });
    }
    onError(message);
    setPhase("idle");
    onBusyChange?.(false);
  }

  async function start() {
    setPhase("preparing");
    onBusyChange?.(true);
    onError("");
    try {
      const prepared = await onPrepare();
      meetingRef.current = prepared;
      setLiveIds({ meetingId: prepared.meetingId, sessionId: prepared.sessionId });

      const capture = new BrowserAudioCapture();
      captureRef.current = capture;

      setPhase("requesting_permission");
      await setSessionStatus(prepared.sessionId, "requesting_permission");

      const sources = await capture.prepare({ systemAudio });
      setSystemAudioActive(sources.systemAudioActive);

      await setSessionStatus(prepared.sessionId, "capturing", {
        started_at: new Date().toISOString(),
        channel_count: 1,
        metadata: {
          microphone: true,
          system_audio: sources.systemAudioActive,
        },
      });
      capture.start({
        partMs: LIVE_PART_MS,
        onPart: (blob, index) => queuePartUpload(blob, index),
      });
      setPhase("capturing");
    } catch (err) {
      const message =
        err instanceof DOMException &&
        (err.name === "NotAllowedError" || err.name === "SecurityError")
          ? "Microphone permission was denied. Allow microphone access and try again."
          : err instanceof Error
            ? err.message
            : "Could not start recording.";
      await failSession(message);
    }
  }

  /**
   * Live parts upload sequentially to preserve part order. One retry, then we
   * keep recording — a lost part shows up as a transcript gap, not a dead
   * capture.
   */
  function queuePartUpload(blob: Blob, index: number) {
    partQueueRef.current = partQueueRef.current
      .then(() => uploadPart(blob, index))
      .catch(() => {});
  }

  async function uploadPart(blob: Blob, index: number) {
    const m = meetingRef.current;
    if (!m) return;
    const supabase = createClient();
    const mime = blob.type || "audio/webm";
    const path = `${m.ownerId}/${m.meetingId}/live/${m.sessionId}/part-${String(
      index,
    ).padStart(5, "0")}`;
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const { error: upErr } = await supabase.storage
          .from(STORAGE_BUCKET)
          .upload(path, blob, { contentType: mime, upsert: true });
        if (upErr) throw new Error(upErr.message);
        const { error: nErr } = await supabase.rpc("notify_capture_part", {
          p_session_id: m.sessionId,
          p_part_index: index,
          p_storage_path: path,
          p_size_bytes: blob.size,
          p_mime_type: mime.split(";")[0],
        });
        if (nErr) throw new Error(nErr.message);
        return;
      } catch {
        if (attempt === 1) setPartFailures((n) => n + 1);
      }
    }
  }

  function pause() {
    captureRef.current?.pause();
    const m = meetingRef.current;
    if (m) void setSessionStatus(m.sessionId, "paused");
    setPhase("paused");
  }

  function resume() {
    captureRef.current?.resume();
    const m = meetingRef.current;
    if (m) void setSessionStatus(m.sessionId, "capturing");
    setPhase("capturing");
  }

  async function stop() {
    const capture = captureRef.current;
    const m = meetingRef.current;
    if (!capture || !m) return;

    setPhase("finalizing");
    try {
      const media = await capture.stop();
      if (media.blob.size === 0) throw new Error("No audio was captured.");
      if (media.blob.size > maxUploadBytes) {
        throw new Error(
          `Recording exceeds the ${formatBytes(maxUploadBytes)} limit.`,
        );
      }

      await setSessionStatus(m.sessionId, "stopped", {
        ended_at: new Date().toISOString(),
        audio_format: media.mimeType,
      });

      const supabase = createClient();
      const { error: beginErr } = await supabase.rpc("begin_meeting_upload", {
        p_meeting_id: m.meetingId,
      });
      if (beginErr) throw new Error(beginErr.message);

      setPhase("uploading");
      await setSessionStatus(m.sessionId, "uploading");
      const { storagePath } = await ingestRecording(supabase, {
        meetingId: m.meetingId,
        ownerId: m.ownerId,
        media,
        fileNameBase: "browser-recording",
        onProgress: setProgress,
      });
      await setSessionStatus(m.sessionId, "uploaded", {
        storage_path: storagePath,
      });
      onBusyChange?.(false);
      onComplete(m.meetingId);
    } catch (err) {
      await failSession(
        err instanceof Error ? err.message : "Recording could not be saved.",
      );
    }
  }

  async function cancel() {
    await captureRef.current?.cancel();
    captureRef.current = null;
    const m = meetingRef.current;
    if (m) {
      await setSessionStatus(m.sessionId, "cancelled", {
        ended_at: new Date().toISOString(),
      });
      await createClient().rpc("cancel_meeting_upload", {
        p_meeting_id: m.meetingId,
      });
    }
    meetingRef.current = null;
    setPhase("idle");
    setElapsed(0);
    setProgress(0);
    onBusyChange?.(false);
    onError("Capture cancelled. The meeting was saved as a draft.");
  }

  if (!supported) {
    return (
      <p className="rounded-md border border-dashed border-border p-4 text-sm text-muted">
        Audio recording is not supported in this browser. Use the upload option
        instead.
      </p>
    );
  }

  const active = phase === "capturing" || phase === "paused";
  const showLive = liveIds !== null && phase !== "idle";

  return (
    <div className="grid gap-4">
    <div className="rounded-md border border-border p-4">
      {phase === "idle" && (
        <div className="grid gap-3">
          <div className="grid gap-2 text-sm">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked
                disabled
                className="accent-teal-700"
              />
              <span>
                Microphone <span className="text-muted">(required)</span>
              </span>
            </label>
            {canShareSystem && (
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={systemAudio}
                  onChange={(e) => setSystemAudio(e.target.checked)}
                  className="accent-teal-700"
                />
                <span>
                  System or meeting audio{" "}
                  <span className="text-muted">(requires a screen/tab share)</span>
                </span>
              </label>
            )}
            <p className="text-xs text-muted">
              {canShareSystem
                ? "System audio works on Chrome/Edge — when prompted, choose “Share tab audio” or a screen with audio. Safari and Firefox may not provide it."
                : "This browser cannot capture system audio; only the microphone will be recorded."}
            </p>
          </div>
          <div>
            <button
              type="button"
              onClick={start}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-strong"
            >
              Start recording
            </button>
          </div>
        </div>
      )}

      {(phase === "preparing" || phase === "requesting_permission") && (
        <p className="text-sm text-muted">
          {phase === "preparing"
            ? "Preparing meeting…"
            : "Waiting for microphone permission…"}
        </p>
      )}

      {active && (
        <div className="grid gap-3">
          <div className="flex items-center gap-3">
            <span className="relative flex h-3 w-3">
              {phase === "capturing" && (
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-500 opacity-75" />
              )}
              <span className="relative inline-flex h-3 w-3 rounded-full bg-red-600" />
            </span>
            <span className="text-sm font-medium">
              {phase === "capturing" ? "Recording" : "Paused"}
            </span>
            <span className="font-mono text-sm tabular-nums">
              {formatMs(elapsed * 1000)}
            </span>
          </div>
          <div className="flex gap-4 text-xs text-muted">
            <span>Microphone ON</span>
            <span>System audio {systemAudioActive ? "ON" : "OFF"}</span>
            {partFailures > 0 && (
              <span className="text-danger">
                {partFailures} live segment
                {partFailures === 1 ? "" : "s"} failed to upload
              </span>
            )}
          </div>
          <div className="flex gap-2">
            {phase === "capturing" ? (
              <button
                type="button"
                onClick={pause}
                className="rounded-md border border-border px-4 py-2 text-sm hover:bg-stone-50"
              >
                Pause
              </button>
            ) : (
              <button
                type="button"
                onClick={resume}
                className="rounded-md border border-border px-4 py-2 text-sm hover:bg-stone-50"
              >
                Resume
              </button>
            )}
            <button
              type="button"
              onClick={stop}
              className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-strong"
            >
              Stop and process
            </button>
            <button
              type="button"
              onClick={cancel}
              className="rounded-md border border-border px-4 py-2 text-sm text-danger hover:bg-stone-50"
            >
              Discard
            </button>
          </div>
        </div>
      )}

      {(phase === "uploading" || phase === "finalizing") && (
        <div className="grid gap-2">
          <p className="text-sm text-muted">
            {phase === "uploading"
              ? `Recording complete. Uploading… ${Math.round(progress * 100)}%`
              : "Finishing recording…"}
          </p>
          {phase === "uploading" && (
            <div
              className="h-2 w-full overflow-hidden rounded-full bg-stone-200"
              role="progressbar"
              aria-valuenow={Math.round(progress * 100)}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div
                className="h-full bg-accent transition-all"
                style={{ width: `${Math.round(progress * 100)}%` }}
              />
            </div>
          )}
        </div>
      )}
    </div>
    {showLive && liveIds && (
      <LiveTranscriptPanel
        meetingId={liveIds.meetingId}
        sessionId={liveIds.sessionId}
      />
    )}
    </div>
  );
}
