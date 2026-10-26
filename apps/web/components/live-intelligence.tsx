"use client";

import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase/client";
import { formatMs } from "@/lib/format";
import type { LiveIntelligenceItem } from "@/lib/types";

const KIND_LABEL: Record<LiveIntelligenceItem["kind"], string> = {
  key_point: "Key point",
  decision: "Decision",
  action_item: "Action item",
  question: "Open question",
};

const KIND_ORDER: LiveIntelligenceItem["kind"][] = [
  "decision",
  "action_item",
  "key_point",
  "question",
];

/**
 * Candidate intelligence for an in-progress capture session. Regenerated
 * wholesale by the worker every few parts — items may appear, change, or
 * disappear until the authoritative final report lands.
 */
export function LiveIntelligencePanel({
  meetingId,
  sessionId,
}: {
  meetingId: string;
  sessionId?: string;
}) {
  const [items, setItems] = useState<LiveIntelligenceItem[]>([]);
  const [visible, setVisible] = useState(false);

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

      const active = ["capturing", "paused", "stopped", "uploading"].includes(
        session.status,
      );
      setVisible(active);

      const { data } = await supabase
        .from("live_intelligence_items")
        .select("*")
        .eq("session_id", session.id)
        .order("run_part_index", { ascending: false })
        .order("item_index");
      if (cancelled || !data) return;

      // Only the newest run's set is authoritative; older runs are stale
      // leftovers if a replace raced a read.
      const latest = Math.max(...data.map((i) => i.run_part_index));
      setItems(
        (data as LiveIntelligenceItem[]).filter(
          (i) => i.run_part_index === latest,
        ),
      );
    }

    void poll();
    const id = setInterval(poll, 8000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [meetingId, sessionId]);

  if (!visible || items.length === 0) return null;

  return (
    <section
      aria-label="Live insights"
      className="rounded-lg border border-dashed border-accent/40 bg-surface"
    >
      <header className="flex items-center gap-2 border-b border-border px-4 py-2">
        <span className="text-sm font-medium">Live insights</span>
        <span className="text-xs text-muted">
          provisional candidates — refreshed as the meeting progresses
        </span>
      </header>
      <div className="grid gap-3 px-4 py-3 sm:grid-cols-2">
        {KIND_ORDER.map((kind) => {
          const group = items.filter((i) => i.kind === kind);
          if (group.length === 0) return null;
          return (
            <div key={kind}>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
                {KIND_LABEL[kind]}s
              </h3>
              <ul className="mt-1 grid gap-1">
                {group.map((i) => (
                  <li key={i.id} className="text-sm italic text-foreground/80">
                    {i.timestamp_ms != null && (
                      <span className="mr-1 font-mono text-xs not-italic text-muted">
                        {formatMs(i.timestamp_ms)}
                      </span>
                    )}
                    {i.content}
                    {i.speaker_label && (
                      <span className="ml-1 text-xs not-italic text-muted">
                        — {i.speaker_label}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
    </section>
  );
}
