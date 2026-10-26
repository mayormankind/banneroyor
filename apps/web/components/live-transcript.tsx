"use client";

import { useEffect, useRef, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { formatMs, speakerDisplayName } from "@/lib/format";

interface LiveSegment {
  id: string;
  speaker_label: string | null;
  start_ms: number;
  text: string;
}

const ACTIVE_STATUSES = new Set([
  "capturing",
  "paused",
  "stopped",
  "uploading",
  "uploaded",
  "processing",
]);

/**
 * Provisional transcript for an in-progress capture session. Polls
 * live_transcript_segments; these rows are always provisional — the final
 * report comes from the batch pipeline after upload.
 */
export function LiveTranscriptPanel({
  meetingId,
  sessionId,
}: {
  meetingId: string;
  /** When known (in-page capture), narrows polling to the active session. */
  sessionId?: string;
}) {
  const [segments, setSegments] = useState<LiveSegment[]>([]);
  const [sessionActive, setSessionActive] = useState(false);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const supabase = createClient();
    let cancelled = false;

    async function poll() {
      let sessionQuery = supabase
        .from("capture_sessions")
        .select("id,status")
        .eq("meeting_id", meetingId)
        .order("created_at", { ascending: false })
        .limit(1);
      if (sessionId) sessionQuery = sessionQuery.eq("id", sessionId);
      const { data: sessions } = await sessionQuery;
      const session = sessions?.[0];
      if (cancelled || !session) return;

      setSessionActive(ACTIVE_STATUSES.has(session.status));

      const { data: segs } = await supabase
        .from("live_transcript_segments")
        .select("id,speaker_label,start_ms,text")
        .eq("session_id", session.id)
        .order("start_ms")
        .order("id")
        .limit(5000);
      if (!cancelled && segs) setSegments(segs as LiveSegment[]);
    }

    void poll();
    const id = setInterval(poll, 4000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [meetingId, sessionId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [segments.length]);

  if (!sessionActive && segments.length === 0) return null;

  return (
    <section
      aria-label="Live transcript"
      className="rounded-lg border border-border bg-surface"
    >
      <header className="flex items-center gap-2 border-b border-border px-4 py-2">
        {sessionActive && (
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-red-500 opacity-75" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-red-600" />
          </span>
        )}
        <span className="text-sm font-medium">Live transcript</span>
        <span className="text-xs text-muted">
          provisional — the final report replaces this
        </span>
      </header>
      <div className="max-h-80 overflow-y-auto px-4 py-3">
        {segments.length === 0 ? (
          <p className="text-sm italic text-muted">
            Speech will appear here shortly…
          </p>
        ) : (
          <ul className="grid gap-2">
            {segments.map((s) => (
              <li key={s.id} className="text-sm">
                <span className="mr-2 font-mono text-xs text-muted">
                  {formatMs(s.start_ms)}
                </span>
                <span className="mr-2 font-medium text-accent-strong">
                  {speakerDisplayName(s.speaker_label, null)}
                </span>
                <span className="italic text-foreground/80">{s.text}</span>
              </li>
            ))}
          </ul>
        )}
        <div ref={bottomRef} />
      </div>
    </section>
  );
}
