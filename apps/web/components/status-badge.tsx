import { MEETING_STATUS_LABEL } from "@/lib/constants";

const STYLE: Record<string, string> = {
  draft: "bg-stone-100 text-stone-600",
  uploading: "bg-sky-100 text-sky-800",
  uploaded: "bg-sky-100 text-sky-800",
  queued: "bg-amber-100 text-amber-800",
  processing: "bg-amber-100 text-amber-800",
  completed: "bg-teal-100 text-teal-800",
  failed: "bg-red-100 text-red-800",
  deleting: "bg-stone-100 text-stone-600",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${STYLE[status] ?? STYLE.draft}`}
    >
      {MEETING_STATUS_LABEL[status] ?? status}
    </span>
  );
}
