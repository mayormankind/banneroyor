export function formatMs(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
}

export function formatDurationSeconds(seconds: number | null): string {
  if (seconds == null) return "—";
  return formatMs(seconds * 1000);
}

export function formatBytes(bytes: number | null): string {
  if (bytes == null) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/** Display name for a diarization label when no participant mapping exists. */
export function speakerDisplayName(
  speakerLabel: string | null,
  participantName: string | null,
): string {
  if (participantName) return participantName;
  if (!speakerLabel) return "Unknown speaker";
  // Provider labels look like "A", "B", ... — map to friendly names.
  if (/^[A-Z]$/.test(speakerLabel)) {
    return `Speaker ${speakerLabel.charCodeAt(0) - 64}`;
  }
  return speakerLabel;
}
