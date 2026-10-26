"use client";

import type { SupabaseClient } from "@supabase/supabase-js";
import { STORAGE_BUCKET } from "../constants";
import type { CapturedMedia } from "./types";

/**
 * Shared ingestion: upload media to private storage via signed URL, then
 * atomically finalize (storage verify → status transition → job → queue).
 * Every capture provider converges here.
 */

const MIME_EXTENSIONS: Record<string, string> = {
  "audio/webm": ".webm",
  "audio/mp4": ".mp4",
  "audio/ogg": ".ogg",
  "audio/mpeg": ".mp3",
  "audio/wav": ".wav",
  "audio/x-wav": ".wav",
  "audio/flac": ".flac",
  "audio/aac": ".aac",
  "video/mp4": ".mp4",
  "video/webm": ".webm",
  "video/quicktime": ".mov",
  "video/x-matroska": ".mkv",
};

export function extensionForMime(mimeType: string): string {
  const base = mimeType.split(";")[0].trim().toLowerCase();
  return MIME_EXTENSIONS[base] ?? ".bin";
}

/** XHR PUT with progress + abort. Returns a promise resolving on 2xx. */
export function uploadToSignedUrl(
  signedUrl: string,
  body: Blob | File,
  onProgress?: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", signedUrl);
    xhr.setRequestHeader("Content-Type", body.type || "application/octet-stream");
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total);
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve();
      else reject(new Error(`Upload failed (HTTP ${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("Network error during upload"));
    xhr.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
    if (signal) {
      if (signal.aborted) {
        xhr.abort();
        return;
      }
      signal.addEventListener("abort", () => xhr.abort(), { once: true });
    }
    xhr.send(body);
  });
}

/**
 * Upload a captured/uploaded recording for a draft meeting and finalize it.
 * Caller must have already run begin_meeting_upload (or the equivalent state
 * transition) — matches the existing upload flow.
 */
export async function ingestRecording(
  supabase: SupabaseClient,
  opts: {
    meetingId: string;
    ownerId: string;
    media: CapturedMedia;
    fileNameBase: string;
    onProgress?: (fraction: number) => void;
    signal?: AbortSignal;
  },
): Promise<{ storagePath: string }> {
  const ext = extensionForMime(opts.media.mimeType);
  const path = `${opts.ownerId}/${opts.meetingId}/${crypto.randomUUID()}-${opts.fileNameBase}${ext}`;

  const { data: signed, error } = await supabase.storage
    .from(STORAGE_BUCKET)
    .createSignedUploadUrl(path);
  if (error) throw new Error(error.message);

  await uploadToSignedUrl(
    signed.signedUrl,
    opts.media.blob,
    opts.onProgress,
    opts.signal,
  );

  const { data: jobId, error: finErr } = await supabase.rpc(
    "finalize_meeting_upload",
    {
      p_meeting_id: opts.meetingId,
      p_file_path: path,
      p_file_name: `${opts.fileNameBase}${ext}`,
      p_mime_type: opts.media.mimeType.split(";")[0],
      p_size_bytes: opts.media.blob.size,
    },
  );
  if (finErr) throw new Error(finErr.message);
  if (!jobId) throw new Error("Finalization returned no job id");
  return { storagePath: path };
}
