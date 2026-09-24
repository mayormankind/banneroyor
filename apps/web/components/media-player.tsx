"use client";

import { useEffect, useState } from "react";

/**
 * Optional synchronized playback: fetches a short-lived signed URL for the
 * private recording. Failure is non-fatal — the report still works.
 */
export function MediaPlayer({ meetingId }: { meetingId: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/meetings/${meetingId}/media`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => {
        if (!cancelled) setUrl(d.url);
      })
      .catch(() => {
        if (!cancelled) setUnavailable(true);
      });
    return () => {
      cancelled = true;
    };
  }, [meetingId]);

  if (unavailable) return null;
  if (!url) {
    return (
      <div className="h-12 animate-pulse rounded-lg border border-border bg-surface" />
    );
  }
  return (
    <audio controls preload="none" src={url} className="w-full">
      Your browser does not support audio playback for this recording.
    </audio>
  );
}
