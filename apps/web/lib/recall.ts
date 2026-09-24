import OpenAI from "openai";
import { z } from "zod";
import { zodTextFormat } from "openai/helpers/zod";
import type { SupabaseClient } from "@supabase/supabase-js";
import { getServerEnv } from "@/lib/env";
import type { RecallAnswer, RecallCitation, SearchResult } from "@/lib/types";

const MAX_EVIDENCE_ITEMS = 40;
const MAX_EVIDENCE_CHARS = 300;

interface EvidenceItem {
  evidence_id: string;
  meeting_id: string;
  meeting_title: string;
  meeting_date: string;
  segment_id: string | null;
  timestamp_ms: number | null;
  kind: string;
  text: string;
}

const answerSchema = z.object({
  sufficient_evidence: z.boolean(),
  answer: z.string(),
  citations: z.array(
    z.object({
      evidence_id: z.string(),
      note: z.string().optional(),
    }),
  ),
});

/** Collect a bounded, ownership-scoped evidence set for a question. */
async function gatherEvidence(
  supabase: SupabaseClient,
  question: string,
): Promise<EvidenceItem[]> {
  const { data: hits, error } = await supabase.rpc("search_meetings", {
    p_query: question,
    p_limit: MAX_EVIDENCE_ITEMS,
  });
  if (error) throw new Error(error.message);
  const results = (hits ?? []) as SearchResult[];
  if (results.length === 0) return [];

  const meetingIds = [...new Set(results.map((r) => r.meeting_id))];
  const { data: meetings } = await supabase
    .from("meetings")
    .select("id, title, meeting_date")
    .in("id", meetingIds);
  const meetingById = new Map(
    (meetings ?? []).map((m) => [m.id, m] as const),
  );

  return results.slice(0, MAX_EVIDENCE_ITEMS).flatMap((r) => {
    const meeting = meetingById.get(r.meeting_id);
    if (!meeting) return [];
    const text = r.excerpt.replace(/<[^>]+>/g, "").slice(0, MAX_EVIDENCE_CHARS);
    return [
      {
        evidence_id: r.result_id,
        meeting_id: r.meeting_id,
        meeting_title: meeting.title,
        meeting_date: meeting.meeting_date,
        segment_id: r.result_type === "transcript" ? r.result_id : null,
        timestamp_ms: r.segment_start_ms,
        kind: r.result_type,
        text,
      },
    ];
  });
}

/** Drop citations that don't reference a retrieved evidence item, and map the
 * surviving ones to full citation objects. Pure — unit-testable. */
export function toCitations(
  raw: { evidence_id: string }[],
  evidence: EvidenceItem[],
): RecallCitation[] {
  const byId = new Map(evidence.map((e) => [e.evidence_id, e]));
  const seen = new Set<string>();
  const citations: RecallCitation[] = [];
  for (const c of raw) {
    const item = byId.get(c.evidence_id);
    if (!item || seen.has(c.evidence_id)) continue;
    seen.add(c.evidence_id);
    citations.push({
      meeting_id: item.meeting_id,
      meeting_title: item.meeting_title,
      meeting_date: item.meeting_date,
      segment_id: item.segment_id,
      timestamp_ms: item.timestamp_ms,
      excerpt: item.text,
    });
  }
  return citations;
}

/**
 * Answer a natural-language question using only retrieved meeting evidence.
 * Every citation is validated against the retrieved set — uncited or
 * fabricated claims are dropped.
 */
export async function answerQuestion(
  supabase: SupabaseClient,
  question: string,
): Promise<RecallAnswer> {
  const evidence = await gatherEvidence(supabase, question);
  if (evidence.length === 0) {
    return {
      answer:
        "Your meeting memory does not contain enough evidence to answer this question.",
      sufficient_evidence: false,
      citations: [],
    };
  }

  const env = getServerEnv();
  const client = new OpenAI({ apiKey: env.OPENAI_API_KEY });

  const evidenceBlock = evidence
    .map(
      (e) =>
        `[${e.evidence_id}] meeting="${e.meeting_title}" date=${e.meeting_date} kind=${e.kind}` +
        (e.timestamp_ms != null ? ` t=${e.timestamp_ms}ms` : "") +
        `\n${e.text}`,
    )
    .join("\n\n");

  const response = await client.responses.parse({
    model: env.OPENAI_RECALL_MODEL,
    instructions:
      "You are the recall engine for Banner Recall, a private meeting memory. " +
      "Answer the user's question ONLY using the evidence excerpts provided. " +
      "Never use outside knowledge. If the evidence is insufficient or " +
      "contradictory, say so plainly and set sufficient_evidence=false. " +
      "Cite the evidence_id of every excerpt your answer relies on. " +
      "Do not invent meetings, dates, speakers, decisions, or facts.",
    input: `Question: ${question}\n\nEvidence:\n${evidenceBlock}`,
    text: { format: zodTextFormat(answerSchema, "recall_answer") },
  });

  const parsed = response.output_parsed;
  if (!parsed) {
    throw new Error("Recall model returned an unparseable answer");
  }

  const citations = toCitations(parsed.citations, evidence);

  return {
    answer: parsed.answer,
    sufficient_evidence: parsed.sufficient_evidence && citations.length > 0,
    citations,
  };
}
