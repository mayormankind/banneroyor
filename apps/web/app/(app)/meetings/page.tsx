import Link from "next/link";
import { createClient } from "@/lib/supabase/server";
import { StatusBadge } from "@/components/status-badge";
import { formatDate, formatDurationSeconds } from "@/lib/format";
import type { Meeting, Participant } from "@/lib/types";

const PAGE_SIZE = 15;

export const metadata = { title: "Meetings" };

interface ListRow extends Meeting {
  participants: Pick<Participant, "display_name">[];
}

export default async function MeetingsPage({
  searchParams,
}: PageProps<"/meetings">) {
  const params = await searchParams;
  const q = (params.q as string) ?? "";
  const status = (params.status as string) ?? "";
  const participant = (params.participant as string) ?? "";
  const page = Math.max(1, Number(params.page) || 1);

  const supabase = await createClient();

  let query = supabase
    .from("meetings")
    .select("*, participants(display_name)", { count: "exact" })
    .order("meeting_date", { ascending: false })
    .range((page - 1) * PAGE_SIZE, page * PAGE_SIZE - 1);

  if (q) {
    query = query.or(`title.ilike.%${q}%,description.ilike.%${q}%`);
  }
  if (status) {
    query = query.eq("status", status);
  }

  const { data, count, error } = await query;
  if (error) {
    throw new Error(error.message);
  }

  let meetings = (data ?? []) as unknown as ListRow[];

  if (participant) {
    // Filter client-side of the page for participant name matches; the list is
    // already scoped small by pagination. For a tighter filter use Search.
    meetings = meetings.filter((m) =>
      m.participants.some((p) =>
        p.display_name.toLowerCase().includes(participant.toLowerCase()),
      ),
    );
  }

  const total = count ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const filterHref = (overrides: Record<string, string>) => {
    const sp = new URLSearchParams({
      q,
      status,
      participant,
      page: String(page),
      ...overrides,
    });
    [...sp.keys()].forEach((k) => !sp.get(k) && sp.delete(k));
    return `/meetings?${sp.toString()}`;
  };

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h1 className="text-xl font-semibold tracking-tight">Meetings</h1>
        <Link
          href="/meetings/new"
          className="rounded-md bg-accent px-3 py-2 text-sm font-medium text-white hover:bg-accent-strong"
        >
          New meeting
        </Link>
      </div>

      <form
        method="get"
        className="mb-4 flex flex-wrap items-end gap-3 rounded-lg border border-border bg-surface p-3"
      >
        <label className="text-sm">
          <span className="mb-1 block text-xs text-muted">Search</span>
          <input
            type="search"
            name="q"
            defaultValue={q}
            placeholder="Title or description"
            className="rounded-md border border-border bg-background px-3 py-1.5 text-sm outline-none focus:border-accent"
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-xs text-muted">Status</span>
          <select
            name="status"
            defaultValue={status}
            className="rounded-md border border-border bg-background px-3 py-1.5 text-sm outline-none focus:border-accent"
          >
            <option value="">All</option>
            <option value="draft">Draft</option>
            <option value="queued">Queued</option>
            <option value="processing">Processing</option>
            <option value="completed">Ready</option>
            <option value="failed">Failed</option>
          </select>
        </label>
        <label className="text-sm">
          <span className="mb-1 block text-xs text-muted">Participant</span>
          <input
            type="text"
            name="participant"
            defaultValue={participant}
            className="rounded-md border border-border bg-background px-3 py-1.5 text-sm outline-none focus:border-accent"
          />
        </label>
        <input type="hidden" name="page" value="1" />
        <button
          type="submit"
          className="rounded-md border border-border px-3 py-1.5 text-sm hover:bg-stone-50"
        >
          Filter
        </button>
        {(q || status || participant) && (
          <Link
            href="/meetings"
            className="px-2 py-1.5 text-sm text-muted hover:text-foreground"
          >
            Clear
          </Link>
        )}
      </form>

      {meetings.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-surface p-10 text-center">
          <h2 className="text-base font-medium">No meetings yet</h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-muted">
            {q || status || participant
              ? "No meetings match these filters."
              : "Upload a meeting recording to get a searchable transcript, summary, decisions, and action items."}
          </p>
          <Link
            href="/meetings/new"
            className="mt-4 inline-block rounded-md bg-accent px-4 py-2 text-sm font-medium text-white hover:bg-accent-strong"
          >
            Create your first meeting
          </Link>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border bg-surface">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
                <th className="px-4 py-2 font-medium">Title</th>
                <th className="hidden px-4 py-2 font-medium md:table-cell">
                  Date
                </th>
                <th className="hidden px-4 py-2 font-medium sm:table-cell">
                  Duration
                </th>
                <th className="hidden px-4 py-2 font-medium lg:table-cell">
                  Participants
                </th>
                <th className="px-4 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {meetings.map((m) => (
                <tr
                  key={m.id}
                  className="border-b border-border last:border-0 hover:bg-stone-50"
                >
                  <td className="px-4 py-2.5">
                    <Link
                      href={`/meetings/${m.id}`}
                      className="font-medium text-foreground hover:text-accent"
                    >
                      {m.title}
                    </Link>
                  </td>
                  <td className="hidden px-4 py-2.5 text-muted md:table-cell">
                    {formatDate(m.meeting_date)}
                  </td>
                  <td className="hidden px-4 py-2.5 text-muted sm:table-cell">
                    {formatDurationSeconds(m.duration_seconds)}
                  </td>
                  <td className="hidden max-w-48 truncate px-4 py-2.5 text-muted lg:table-cell">
                    {m.participants.map((p) => p.display_name).join(", ") ||
                      "—"}
                  </td>
                  <td className="px-4 py-2.5">
                    <StatusBadge status={m.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {totalPages > 1 && (
        <nav
          aria-label="Pagination"
          className="mt-4 flex items-center justify-between text-sm"
        >
          <span className="text-muted">
            Page {page} of {totalPages} · {total} meetings
          </span>
          <div className="flex gap-2">
            {page > 1 && (
              <Link
                href={filterHref({ page: String(page - 1) })}
                className="rounded-md border border-border px-3 py-1.5 hover:bg-stone-50"
              >
                Previous
              </Link>
            )}
            {page < totalPages && (
              <Link
                href={filterHref({ page: String(page + 1) })}
                className="rounded-md border border-border px-3 py-1.5 hover:bg-stone-50"
              >
                Next
              </Link>
            )}
          </div>
        </nav>
      )}
    </div>
  );
}
