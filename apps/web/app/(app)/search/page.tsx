import Link from "next/link";
import { createClient } from "@/lib/supabase/server";
import { RecallPanel } from "@/components/recall-panel";
import { formatDate, formatMs } from "@/lib/format";
import type { SearchResult } from "@/lib/types";

export const metadata = { title: "Search & Recall" };

const RESULT_TYPE_LABEL: Record<string, string> = {
  meeting: "Meeting",
  summary: "Summary",
  key_point: "Key point",
  decision: "Decision",
  action_item: "Action item",
  question: "Question",
  transcript: "Transcript",
};

const PAGE_SIZE = 50;

export default async function SearchPage({
  searchParams,
}: PageProps<"/search">) {
  const params = await searchParams;
  const q = (params.q as string) ?? "";
  const type = (params.type as string) ?? "";
  const from = (params.from as string) ?? "";
  const to = (params.to as string) ?? "";
  const participant = (params.participant as string) ?? "";
  const page = Math.max(1, Number(params.page) || 1);

  let results: SearchResult[] = [];
  let error: string | null = null;

  if (q.trim()) {
    const supabase = await createClient();
    const { data, error: rpcError } = await supabase.rpc("search_meetings", {
      p_query: q,
      p_types: type ? [type] : null,
      p_date_from: from ? new Date(from).toISOString() : null,
      p_date_to: to ? new Date(`${to}T23:59:59`).toISOString() : null,
      p_participant: participant || null,
      p_limit: PAGE_SIZE,
      p_offset: (page - 1) * PAGE_SIZE,
    });
    if (rpcError) error = rpcError.message;
    else results = (data ?? []) as SearchResult[];
  }

  const grouped = new Map<string, SearchResult[]>();
  for (const r of results) {
    const list = grouped.get(r.meeting_id) ?? [];
    list.push(r);
    grouped.set(r.meeting_id, list);
  }

  const filterHref = (overrides: Record<string, string>) => {
    const sp = new URLSearchParams({
      q,
      type,
      from,
      to,
      participant,
      page: String(page),
      ...overrides,
    });
    [...sp.keys()].forEach((k) => !sp.get(k) && sp.delete(k));
    return `/search?${sp.toString()}`;
  };

  return (
    <div className="grid gap-8">
      <section aria-labelledby="recall-heading">
        <h1 id="recall-heading" className="mb-1 text-xl font-semibold tracking-tight">
          Search &amp; Recall
        </h1>
        <p className="mb-4 text-sm text-muted">
          Ask a question across your meetings, or search transcript text,
          summaries, decisions, and action items.
        </p>
        <RecallPanel />
      </section>

      <section aria-labelledby="search-heading">
        <h2 id="search-heading" className="mb-3 text-base font-semibold">
          Full-text search
        </h2>
        <form
          method="get"
          className="mb-4 flex flex-wrap items-end gap-3 rounded-lg border border-border bg-surface p-3"
        >
          <label className="min-w-56 flex-1 text-sm">
            <span className="mb-1 block text-xs text-muted">Query</span>
            <input
              type="search"
              name="q"
              required
              defaultValue={q}
              placeholder="e.g. pagination decision"
              className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm outline-none focus:border-accent"
            />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-muted">Type</span>
            <select
              name="type"
              defaultValue={type}
              className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
            >
              <option value="">Everything</option>
              {Object.entries(RESULT_TYPE_LABEL).map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-muted">From</span>
            <input
              type="date"
              name="from"
              defaultValue={from}
              className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
            />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-muted">To</span>
            <input
              type="date"
              name="to"
              defaultValue={to}
              className="rounded-md border border-border bg-background px-2 py-1.5 text-sm"
            />
          </label>
          <label className="text-sm">
            <span className="mb-1 block text-xs text-muted">Participant</span>
            <input
              type="text"
              name="participant"
              defaultValue={participant}
              className="w-32 rounded-md border border-border bg-background px-2 py-1.5 text-sm"
            />
          </label>
          <input type="hidden" name="page" value="1" />
          <button
            type="submit"
            className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-strong"
          >
            Search
          </button>
        </form>

        {error && (
          <p role="alert" className="text-sm text-danger">
            Search failed: {error}
          </p>
        )}

        {q.trim() && !error && (
          results.length === 0 ? (
            <p className="rounded-lg border border-dashed border-border bg-surface p-6 text-sm text-muted">
              No results for “{q}”.
            </p>
          ) : (
            <div className="grid gap-4">
              {[...grouped.entries()].map(([meetingId, hits]) => (
                <div
                  key={meetingId}
                  className="rounded-lg border border-border bg-surface p-4"
                >
                  <div className="mb-2 flex items-baseline justify-between">
                    <Link
                      href={`/meetings/${meetingId}`}
                      className="font-medium text-foreground hover:text-accent"
                    >
                      {hits[0].meeting_title}
                    </Link>
                    <span className="text-xs text-muted">
                      {formatDate(hits[0].meeting_date)}
                    </span>
                  </div>
                  <ul className="space-y-1.5">
                    {hits.map((h) => (
                      <li key={`${h.result_type}-${h.result_id}`} className="text-sm">
                        <span className="mr-2 rounded bg-stone-100 px-1.5 py-0.5 text-xs text-muted">
                          {RESULT_TYPE_LABEL[h.result_type] ?? h.result_type}
                        </span>
                        <span
                          // ts_headline emits <mark> tags for matches; this is
                          // server-generated HTML, not user input.
                          dangerouslySetInnerHTML={{ __html: h.excerpt }}
                        />
                        {h.result_type === "transcript" &&
                          h.segment_start_ms != null && (
                            <Link
                              href={`/meetings/${meetingId}#seg-${h.result_id}`}
                              className="ml-2 whitespace-nowrap text-xs text-accent hover:underline"
                            >
                              @ {formatMs(h.segment_start_ms)}
                            </Link>
                          )}
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )
        )}

        {results.length === PAGE_SIZE && (
          <div className="mt-4">
            <Link
              href={filterHref({ page: String(page + 1) })}
              className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-stone-50"
            >
              Next page
            </Link>
          </div>
        )}
      </section>
    </div>
  );
}
