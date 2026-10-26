# Near-live transcription — technical design

Scope: produce a **provisional** transcript while a browser capture is running,
without new infrastructure and without touching the authoritative batch path.

## Transport

Rejected: WebSocket/WebRTC audio streaming to a live ASR — new infra, new auth
surface, and the OpenAI realtime API doesn't provide diarization today.

Chosen (fits existing infra, verified primitives only):

```
MediaRecorder(timeslice=20s)
  -> part-N blob uploaded to storage  {owner}/{meeting}/live/{session}/part-%05d
  -> rpc notify_capture_part (part row + PGMQ message, atomic)
  -> worker sync job: concat parts -> slice new audio -> transcribe
  -> live_transcript_segments (provisional)
  -> browser polls every ~4s (same pattern as StatusPoller)
```

Latency: ~20–40s end-to-end. Honest "near-live," not sub-second.

## Chunk integrity — the key constraint

MediaRecorder timeslice chunks are **fragments of one continuous container**,
not standalone files — part N>0 is missing the container init segment and is
not independently decodable. Two ways around this:

- Re-mux / per-part recorder restart → boundary gaps or fragile tricks. No.
- **Byte-concatenate parts** → reconstructs the exact original stream,
  decodable by ffmpeg. Chosen. The worker slices the not-yet-transcribed tail
  out of the concatenated file.

The worker keeps a per-session concat cache (`{TEMP_DIR}/live_{session}.webm`)
persisted across sync jobs; if missing/corrupt it rebuilds from all parts.

## Ordering & idempotency

`capture_parts` has `unique(session_id, part_index)` — client retries dedupe.

The session row carries the cursor: `transcribed_part_index` (last processed,
starts at -1) and `transcribed_ms_end`. A sync:

1. Reads parts ascending; the run is the contiguous tail
   `cursor+1 .. last available index`. No gap processing.
2. Computes `start_ms = session.transcribed_ms_end`, downloads/appends new
   parts to the concat cache, `ffprobe`s the total duration = `end_ms`.
3. ffmpeg `-ss start -to end` slice → transcribe (diarized) once per run —
   a whole run in one call, not per part (speaker continuity + fewer calls).
4. Commits via RPC `commit_capture_sync` (security definer, single txn):
   - `SELECT ... FOR UPDATE` the session; if `transcribed_part_index` ≠
     expected → return false (concurrent sync won; converge via next message)
   - delete segments for the run key + insert new ones (retry-safe)
   - mark parts processed, advance cursor + `transcribed_ms_end`

Racing syncs: the CAS means only one commits; losers re-read and see nothing
new. Crash between steps 3–4 → nothing committed, next sync retries the same
run. Duplicate `capture_sync` messages are no-ops (run already processed).

## Timestamps

Offsets come from **decoded durations of the actual audio** (ffprobe of the
concat), not wall-clock or part counts — immune to pause/resume, drift, and
browser timing jitter. Provider segment offsets are added on top.

## Transcript continuity

Each sync slice is diarized independently — speaker labels may drift across
syncs ("Speaker 1" in slice 3 ≠ slice 4). Accepted for the provisional view;
the UI labels it provisional. The **final pass** re-diarizes the complete file
in the existing batch pipeline and is authoritative.

## Reconciliation

- Provisional text lives only in `live_transcript_segments` — never merged
  into `transcript_segments`.
- On stop, the full blob uploads through the existing path →
  `finalize_meeting_upload` → authoritative transcript/intelligence.
- Session marked `completed` when the meeting completes.

## Retries

- Part upload: 1 client-side retry, then continues recording (a lost part
  shows as a gap, not a dead session — `metadata.live_error` records it).
- Sync job: PGMQ redelivery via visibility timeout; `read_ct` bound → archive.
  Parts stay `uploaded` so the next part's message converges anyway.

## Cost control

One diarized transcribe call per ~20s of new audio (same per-second cost as
batch).

## Live intelligence (added in 0011)

After each committed transcript sync, the worker may regenerate **candidate**
intelligence — throttled by `LIVE_INTEL_PART_INTERVAL` (default 3 parts ≈ 60s
of new audio) to bound LLM spend. Each run re-analyzes the accumulated
provisional transcript and commits via `commit_capture_intel` (service-role
RPC, CAS on `capture_sessions.intel_part_index`): the session's
`live_intelligence_items` set is deleted and replaced atomically — candidates
are cumulative snapshots, not an append log. Intel failures never block or
retry transcript sync. Items carry `kind`, `content`, `speaker_label`,
`timestamp_ms`, and `evidence_segment_indices` (indices into the live
transcript render order — not `transcript_segments` ids). The batch report
remains authoritative; live items are display-only.
