# Banner Recall

*Meetings remembered. Decisions preserved. Actions clear.*

A private meeting-intelligence and meeting-memory application. Upload an audio
or video recording; Banner Recall produces a timestamped, speaker-labeled
transcript plus an evidence-grounded report — summary, key points, decisions,
action items, and open questions — and makes everything searchable and
recallable later.

## Architecture at a glance

```
Next.js web app (apps/web)          Python worker (apps/worker)
  ├── Supabase Auth                     ├── PGMQ queue consumer
  ├── meeting CRUD + upload             ├── FFmpeg media pipeline
  ├── report / history / search UI      ├── OpenAI diarized transcription
  └── grounded recall                   └── structured meeting intelligence
                │                                   │
                └──── Supabase Postgres + private Storage + PGMQ ────┘
```

- **Web:** Next.js (App Router), React, TypeScript, Tailwind — `apps/web`
- **Worker:** Python + FFmpeg + OpenAI SDK — `apps/worker`
- **Data:** Supabase Postgres (RLS everywhere), private Storage bucket, PGMQ
  queue — `supabase/migrations`

## Quick start

See **[docs/development.md](docs/development.md)** for the full walkthrough.
Short version:

```bash
# 1. Database (Supabase local dev)
supabase start && supabase db reset

# 2. Web app
cd apps/web && cp ../../.env.example .env.local   # fill in values
npm install && npm run dev

# 3. Worker (separate terminal)
cd apps/worker && cp ../../.env.example .env
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
WORKER_FAKE_PROVIDERS=true .venv/Scripts/python -m app.main
```

## Documentation

- [docs/architecture.md](docs/architecture.md) — data flow, schema, queue, pipeline
- [docs/development.md](docs/development.md) — local setup, tests, mocked pipeline
- [docs/deployment.md](docs/deployment.md) — Supabase, Vercel, Render
- [docs/privacy-and-data.md](docs/privacy-and-data.md) — consent, retention, deletion
- [docs/evaluation.md](docs/evaluation.md) — test/evaluation plan
