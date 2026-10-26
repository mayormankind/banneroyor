# Deployment

Three deployables: Supabase project, Vercel (web), Render (worker).

## Supabase

1. Create a project at supabase.com (or use local for dev).
2. Apply migrations:

   ```bash
   supabase link --project-ref <ref>
   supabase db push          # applies supabase/migrations/*
   ```

   This creates all tables, RLS policies, the private `meeting-recordings`
   bucket, the `meeting-processing` PGMQ queue, and all RPCs.

3. Verify: in the dashboard, confirm the bucket exists and is **private**,
   `pgmq.list_queues()` returns `meeting-processing`, and RLS is enabled on all
   public tables.
4. Auth: enable Email provider; set Site URL to the deployed origin; add
   `https://<domain>/auth/callback` to redirect URLs.

## Vercel (apps/web)

- Import the repo; set **Root Directory** to `apps/web`.
- Build command `npm run build`, install `npm install` (defaults work).
- Environment variables:

  | Var | Value |
  | --- | ----- |
  | `NEXT_PUBLIC_SUPABASE_URL` | project URL |
  | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | anon key |
  | `OPENAI_API_KEY` | for recall answers |
  | `OPENAI_RECALL_MODEL` | e.g. `gpt-4o-mini` |
  | `MAX_UPLOAD_BYTES` | e.g. `2147483648` |
  | `MEDIA_URL_TTL_SECONDS` | e.g. `600` |

- No long-running work happens in Vercel functions; processing is always
  queued to the worker.

## Render (apps/worker)

Create a **Background Worker**:

- Runtime: Docker
- Dockerfile path: `apps/worker/Dockerfile` (installs ffmpeg + Python deps)
- Start command: baked in (`python -m app.main`)
- Environment variables:

  | Var | Value |
  | --- | ----- |
  | `WORKER_ENV` | `production` |
  | `SUPABASE_URL` | project URL |
  | `SUPABASE_SERVICE_ROLE_KEY` | service-role key (secret) |
  | `SUPABASE_STORAGE_BUCKET` | `meeting-recordings` |
  | `QUEUE_NAME` | `meeting-processing` |
  | `OPENAI_API_KEY` | secret |
  | `OPENAI_TRANSCRIPTION_MODEL` | `gpt-4o-transcribe-diarize` |
  | `OPENAI_INTELLIGENCE_MODEL` | `gpt-4o-mini` |
  | `MAX_JOB_ATTEMPTS` | `4` |
  | `WORKER_CONCURRENCY` | `1` (raise with plan size) |
  | `QUEUE_VISIBILITY_TIMEOUT_SECONDS` | `900` |
  | `TRANSCRIBE_CHUNK_SECONDS` | `600` |

- Health: the process is a poll loop; Render restarts on exit. Graceful
  shutdown on SIGTERM finishes in-flight messages.
- Recovery: failed messages land in `pgmq.a_meeting-processing`; requeue via
  `request_meeting_retry` (UI) or `enqueue_meeting_job` (SQL).

## OpenAI

- `gpt-4o-transcribe-diarize` — diarized transcription (Transcription API,
  `response_format=diarized_json`, `chunking_strategy=auto`).
- `gpt-4o-mini` (default) — structured intelligence + recall. Configure via
  `OPENAI_INTELLIGENCE_MODEL` / `OPENAI_RECALL_MODEL`.
- Provider file-size ceiling used for chunking decisions: ~24 MB per request
  (`TRANSCRIBE_MAX_FILE_BYTES`); audio is also split at ~10-minute boundaries
  on silence points.
