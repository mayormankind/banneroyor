# Local development

## Prerequisites

- Node.js 20.9+ and npm 10+
- Python 3.11+ (3.12 recommended)
- FFmpeg (`ffmpeg`, `ffprobe` on PATH) for real media processing — optional for
  unit tests, required for real transcription runs
- Supabase CLI (`npm i -g supabase` or `scoop install supabase`)
- Docker (for `supabase start`)

## Setup

```bash
# 1. Start local Supabase (applies migrations on db reset)
supabase start
supabase db reset          # runs supabase/migrations/*

# 2. Web app
cd apps/web
npm install
cp ../../.env.example .env.local
#   NEXT_PUBLIC_SUPABASE_URL=http://127.0.0.1:54321
#   NEXT_PUBLIC_SUPABASE_ANON_KEY=<from `supabase status`>
#   OPENAI_API_KEY=...      # only needed for the recall feature
npm run dev               # http://localhost:3000

# 3. Worker
cd apps/worker
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt
cp ../../.env.example .env
#   SUPABASE_URL=http://127.0.0.1:54321
#   SUPABASE_SERVICE_ROLE_KEY=<service_role from `supabase status`>
python -m app.main
```

### Mocked end-to-end pipeline (no OpenAI key)

Set `WORKER_FAKE_PROVIDERS=true` in `apps/worker/.env`. The worker will skip
OpenAI and produce a deterministic transcript + report. This is refused when
`WORKER_ENV=production`.

> The fake path still runs the real FFmpeg media stage, so a real audio file
> exercises everything except the provider calls. To skip FFmpeg too, tests
> monkeypatch `prepare_audio_chunks` (see `tests/test_pipeline.py`).

## Tests & quality gates

```bash
# web
cd apps/web
npm run typecheck    # tsc --noEmit (run `npx next typegen` first if needed)
npm run lint         # eslint
npm run test         # vitest
npm run build        # production build

# worker
cd apps/worker
.venv/Scripts/python -m pytest        # 36 tests, no network/ffmpeg/OpenAI
.venv/Scripts/python -m ruff check app tests
.venv/Scripts/python -c "import app.main"   # startup import check
```

## Useful operations

- **Queue backlog:** `select * from pgmq.metrics('meeting-processing');` in
  Supabase Studio / SQL editor.
- **Requeue a failed job:** use "Retry processing" on the meeting page (calls
  `request_meeting_retry`), or `select enqueue_meeting_job('<meeting>', '<job>', 'process_meeting', 0)` as service role.
- **Dead messages:** archived messages live in `pgmq.a_meeting-processing`.
- **Stuck running job:** set `processing_jobs.status='queued'` (service role)
  and re-enqueue a message; persistence is idempotent per run.

## Troubleshooting

- *Upload fails immediately:* bucket missing → re-run migrations; file too
  large → check `MAX_UPLOAD_BYTES` vs Supabase `file_size_limit`.
- *Job never picked up:* worker not running, or `read_meeting_jobs` RPC
  failing (service-role key missing/incorrect).
- *`invalid meeting status transition`:* a client tried to write `status`
  directly — pipeline fields are read-only; use the RPC flow.
