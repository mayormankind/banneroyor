# Architecture

## Data flow

```
Browser
  │  Supabase Auth (email/password, SSR cookies via proxy.ts)
  ▼
Next.js (apps/web) ── user-scoped Supabase client (RLS enforced)
  │  create meeting → participants → begin_meeting_upload
  │  direct signed-URL upload → finalize_meeting_upload (atomic:
  │  validates storage object, writes source fields, creates
  │  processing_jobs row, pgmq.send, status → queued)
  ▼
Supabase: Postgres + private bucket `meeting-recordings` + PGMQ
  ▼
Python worker (apps/worker) — Render background worker
  claim (pgmq.read via read_meeting_jobs RPC)
    → validate job + meeting
    → download recording (service role)
    → ffprobe inspect → ffmpeg normalize (16 kHz mono mp3)
    → silence-aware split when > chunk limits
    → OpenAI gpt-4o-transcribe-diarize (response_format=diarized_json,
      chunking_strategy=auto, optional known_speaker_*)
    → normalize to ms segments, merge chunk timelines
    → persist transcript + participant↔speaker mapping (run-scoped)
    → chunked structured intelligence (Responses API, pydantic schema)
    → persist summary/key_points/decisions/action_items/questions
    → meetings.current_run_id ← run_id, status → completed
    → ack message (or postpone w/ backoff / archive terminal)
  ▼
Report UI: transcript, speaker mapping, edits, evidence links
Search: Postgres FTS via search_meetings RPC
Recall: FTS retrieval → bounded evidence → OpenAI answer with
        citations validated against retrieved rows
```

## Key design decisions

- **Run-scoped results.** Every transcript segment and intelligence row carries
  `run_id`; `meetings.current_run_id` advances only after a whole run
  validates and persists. A failed reprocess never corrupts a good report, and
  duplicate queue deliveries cannot duplicate visible data (unique key
  `(meeting_id, run_id, segment_index)`; claim is a conditional status update).
- **Queue isolation.** The worker never calls pgmq directly — all queue access
  goes through `read_meeting_jobs` / `postpone_meeting_job_message` /
  `delete_meeting_job_message` / `archive_meeting_job_message` RPCs behind
  `QueueAdapter`, so a different durable queue can replace PGMQ without
  touching pipeline code.
- **Status machine.** `meetings.status` transitions are enforced by a trigger;
  pipeline-owned columns (status, source_*, run pointers) are read-only for end
  users via column grants *and* a trigger. App RPCs pass a transaction-scoped
  GUC (`banner_recall.internal`) to perform privileged transitions.
- **Identity vs diarization.** Provider diarization yields raw labels (`A`,
  `B`, …). The worker creates a `participants` row per label (display name
  `Speaker N`, or the known-speaker name when OpenAI verified a reference
  clip). Users rename participants; the raw label is never rewritten.
- **Evidence.** The intelligence model cites transcript *indices* it can see;
  the worker maps indices → persisted segment UUIDs and drops out-of-range
  references. Recall citations are likewise validated server-side.
- **Media access.** Recordings live in a private bucket under
  `{owner_id}/{meeting_id}/…`; playback uses short-lived signed URLs minted
  only after an ownership check (`/api/meetings/[id]/media`).

## Deferred / extension points

- pgvector semantic search (FTS remains the baseline; add an `embeddings`
  table + RPC and blend candidates in `gatherEvidence`)
- Live capture / platform bots (Zoom/Meet/Teams) — the queue and pipeline are
  source-agnostic; add a `source_type` and an ingestion producer
- Realtime status push (the report polls with `router.refresh()` today; a
  Supabase Realtime channel can replace the poller without schema changes)
