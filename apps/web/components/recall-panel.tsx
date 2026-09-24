"use client";

import { useState } from "react";
import Link from "next/link";
import { formatDate, formatMs } from "@/lib/format";
import type { RecallAnswer } from "@/lib/types";

export function RecallPanel() {
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [answer, setAnswer] = useState<RecallAnswer | null>(null);

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim()) return;
    setPending(true);
    setError(null);
    setAnswer(null);
    try {
      const res = await fetch("/api/recall", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ question: question.trim() }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Recall failed");
      setAnswer(data as RecallAnswer);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Recall failed");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="rounded-lg border border-border bg-surface p-4">
      <form onSubmit={ask} className="flex gap-2">
        <input
          type="text"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask about your meetings, e.g. “What did we decide about pagination?”"
          aria-label="Recall question"
          className="flex-1 rounded-md border border-border bg-background px-3 py-2 text-sm outline-none focus:border-accent"
        />
        <button
          type="submit"
          disabled={pending || question.trim().length < 3}
          className="rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-strong disabled:opacity-60"
        >
          {pending ? "Thinking…" : "Ask"}
        </button>
      </form>

      {error && (
        <p role="alert" className="mt-3 text-sm text-danger">
          {error}
        </p>
      )}

      {answer && (
        <div className="mt-4">
          <p className="whitespace-pre-wrap text-sm leading-relaxed">
            {answer.answer}
          </p>
          {!answer.sufficient_evidence && (
            <p className="mt-2 text-xs text-muted">
              This answer is limited by the evidence found in your meetings.
            </p>
          )}
          {answer.citations.length > 0 && (
            <div className="mt-3 border-t border-border pt-3">
              <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted">
                Sources
              </p>
              <ul className="space-y-2">
                {answer.citations.map((c, i) => (
                  <li key={i} className="text-xs">
                    <Link
                      href={
                        c.segment_id
                          ? `/meetings/${c.meeting_id}#seg-${c.segment_id}`
                          : `/meetings/${c.meeting_id}`
                      }
                      className="font-medium text-accent hover:underline"
                    >
                      {c.meeting_title}
                    </Link>
                    <span className="text-muted">
                      {" "}
                      · {formatDate(c.meeting_date)}
                      {c.timestamp_ms != null
                        ? ` · @ ${formatMs(c.timestamp_ms)}`
                        : ""}
                    </span>
                    <p className="mt-0.5 text-muted">“{c.excerpt}”</p>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
