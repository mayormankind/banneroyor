export const PRODUCT_NAME = "Banner Recall";
export const PRODUCT_TAGLINE =
  "Meetings remembered. Decisions preserved. Actions clear.";

export const STORAGE_BUCKET = "meeting-recordings";

export const ACCEPTED_MEDIA_TYPES: Record<string, string> = {
  "audio/mpeg": ".mp3",
  "audio/mp4": ".m4a",
  "audio/x-m4a": ".m4a",
  "audio/wav": ".wav",
  "audio/x-wav": ".wav",
  "audio/webm": ".webm",
  "audio/ogg": ".ogg",
  "audio/flac": ".flac",
  "audio/aac": ".aac",
  "video/mp4": ".mp4",
  "video/webm": ".webm",
  "video/quicktime": ".mov",
  "video/x-matroska": ".mkv",
};

export const ACCEPTED_EXTENSIONS = [
  ".mp3",
  ".m4a",
  ".wav",
  ".webm",
  ".ogg",
  ".flac",
  ".aac",
  ".mp4",
  ".mov",
  ".mkv",
];

export const ACTIVE_MEETING_STATUSES = ["uploading", "uploaded", "queued", "processing"];

export const MEETING_STATUS_LABEL: Record<string, string> = {
  draft: "Draft",
  uploading: "Uploading",
  uploaded: "Uploaded",
  queued: "Queued",
  processing: "Processing",
  completed: "Ready",
  failed: "Failed",
  deleting: "Deleting",
};

export const STAGE_LABEL: Record<string, string> = {
  validate: "Validating meeting",
  download: "Downloading recording",
  inspect: "Inspecting media",
  normalize: "Preparing audio",
  transcribe: "Transcribing",
  speakers: "Mapping speakers",
  persist_transcript: "Saving transcript",
  intelligence: "Generating report",
  persist_results: "Saving results",
};
