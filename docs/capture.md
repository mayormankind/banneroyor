# Capture

Banner Recall treats capture as a provider layer. A meeting is the logical
entity; a **capture session** is one attempt at obtaining its media.

## Provider contract

Every source — upload, browser, desktop, Google Meet, Zoom, Teams — converges
on the same ingestion contract:

```
produce media (file/Blob)
    -> createSignedUploadUrl -> PUT to private storage
    -> finalize_meeting_upload (atomic: verify + job + queue)
    -> existing processing pipeline
```

No source gets its own transcription or intelligence pipeline. The worker's
`validate` stage accepts any `source_type` that has a `source_file_path`.

- Interface: `apps/web/lib/capture/types.ts` (`CaptureProvider`,
  `CaptureCapabilities`, `CapturedMedia`).
- Shared ingestion: `apps/web/lib/capture/ingest.ts` (`ingestRecording`,
  `uploadToSignedUrl`).
- Providers declare capabilities explicitly (`supportsSystemAudio`,
  `supportsAutomaticStart`, ...) and never implement operations they can't
  support.

## capture_sessions

Defined in `supabase/migrations/0009_capture_sessions.sql`. Tracks lifecycle
independent of meeting status:

`created -> requesting_permission -> capturing -> paused -> stopped ->
uploading -> uploaded` (then the meeting row takes over:
`processing -> completed`). Terminal-side states: `failed`, `cancelled`.

RLS: owner-scoped (`owner_id = auth.uid()`), all operations.

Uploads do not currently create a session row — they are implicit
`source_type='upload'` meetings. Browser capture creates one per attempt.

## Browser provider

`apps/web/lib/capture/recorder.ts` (`BrowserAudioCapture`) +
`apps/web/components/capture-recorder.tsx` (UI).

- Microphone via `getUserMedia` (required).
- Optional system/meeting audio via `getDisplayMedia` audio, merged with the
  mic through `AudioContext`/`MediaStreamDestination`.
- `MediaRecorder` with 1s `timeslice` — chunks collected into a single Blob at
  stop; the timeslice keeps the door open for near-live chunk streaming.
- Pause/resume map to `MediaRecorder.pause()/resume()`.
- Recorded format: first supported of webm/opus, webm, mp4, ogg/opus, ogg.

### Honest limits (communicated in the UI)

- System/tab audio is only reliably available on Chromium; the user must pick
  "share tab audio" (or a screen with audio on Windows) in the share prompt.
  Safari/Firefox usually yield no audio track — the UI shows
  `System audio OFF` in that case rather than pretending.
- Long recordings are bounded by `MAX_UPLOAD_BYTES` (checked before upload).
- Recording does not survive a full page reload — the mic stream is tied to
  the tab. Stop-and-upload before closing.

## Flow

New meeting form (`components/new-meeting-form.tsx`) has two modes:

- **Upload a file** — unchanged existing path.
- **Record in browser** — `prepareCapture` creates the meeting
  (`source_type='browser'`) + `capture_sessions` row, then the recorder owns
  the session lifecycle: permissions -> capture -> stop -> upload -> finalize.
  Discard marks the session `cancelled` and returns the meeting to `draft`.

## Roadmap

Desktop (Tauri/Electron thin client), Google Meet/Zoom/Teams native
integrations, and calendar-triggered capture plug in as additional
`CaptureProvider` implementations emitting the same `CapturedMedia`.
