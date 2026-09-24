-- 0003: meeting intelligence tables and processing job bookkeeping
--
-- All intelligence rows carry run_id + generation_version so a reprocessing run
-- never overwrites the currently displayed report. meetings.current_run_id only
-- advances once a full run has validated and persisted.

create table public.summaries (
  id uuid primary key default gen_random_uuid(),
  meeting_id uuid not null references public.meetings (id) on delete cascade,
  owner_id uuid not null references public.profiles (id) on delete cascade,
  run_id uuid not null,
  summary text not null,
  generation_version text not null,
  model text,
  is_edited boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index summaries_meeting_idx on public.summaries (meeting_id, run_id);

create trigger summaries_updated_at
  before update on public.summaries
  for each row execute function public.set_updated_at();

create table public.key_points (
  id uuid primary key default gen_random_uuid(),
  meeting_id uuid not null references public.meetings (id) on delete cascade,
  owner_id uuid not null references public.profiles (id) on delete cascade,
  run_id uuid not null,
  content text not null,
  sort_order integer not null default 0,
  evidence_segment_ids uuid[] not null default '{}',
  timestamp_ms bigint,
  generation_version text not null,
  is_edited boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index key_points_meeting_idx on public.key_points (meeting_id, run_id);

create trigger key_points_updated_at
  before update on public.key_points
  for each row execute function public.set_updated_at();

create table public.decisions (
  id uuid primary key default gen_random_uuid(),
  meeting_id uuid not null references public.meetings (id) on delete cascade,
  owner_id uuid not null references public.profiles (id) on delete cascade,
  run_id uuid not null,
  description text not null,
  speaker_label text,
  participant_id uuid references public.participants (id) on delete set null,
  evidence_segment_ids uuid[] not null default '{}',
  timestamp_ms bigint,
  generation_version text not null,
  is_edited boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index decisions_meeting_idx on public.decisions (meeting_id, run_id);

create trigger decisions_updated_at
  before update on public.decisions
  for each row execute function public.set_updated_at();

create table public.action_items (
  id uuid primary key default gen_random_uuid(),
  meeting_id uuid not null references public.meetings (id) on delete cascade,
  owner_id uuid not null references public.profiles (id) on delete cascade,
  run_id uuid not null,
  description text not null,
  owner_participant_id uuid references public.participants (id) on delete set null,
  owner_speaker_label text,
  due_date date,
  status text not null default 'open' check (status in ('open', 'in_progress', 'completed', 'cancelled')),
  evidence_segment_ids uuid[] not null default '{}',
  timestamp_ms bigint,
  generation_version text not null,
  is_edited boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index action_items_meeting_idx on public.action_items (meeting_id, run_id);

create trigger action_items_updated_at
  before update on public.action_items
  for each row execute function public.set_updated_at();

create table public.questions (
  id uuid primary key default gen_random_uuid(),
  meeting_id uuid not null references public.meetings (id) on delete cascade,
  owner_id uuid not null references public.profiles (id) on delete cascade,
  run_id uuid not null,
  question text not null,
  resolved boolean not null default false,
  evidence_segment_ids uuid[] not null default '{}',
  timestamp_ms bigint,
  generation_version text not null,
  is_edited boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index questions_meeting_idx on public.questions (meeting_id, run_id);

create trigger questions_updated_at
  before update on public.questions
  for each row execute function public.set_updated_at();

create table public.processing_jobs (
  id uuid primary key default gen_random_uuid(),
  meeting_id uuid not null references public.meetings (id) on delete cascade,
  owner_id uuid not null references public.profiles (id) on delete cascade,
  job_type text not null default 'process_meeting',
  status text not null default 'queued' check (status in ('queued', 'running', 'succeeded', 'failed', 'dead')),
  attempts integer not null default 0,
  max_attempts integer not null default 4,
  current_stage text,
  progress real check (progress is null or (progress >= 0 and progress <= 1)),
  run_id uuid,
  idempotency_key text,
  error_code text,
  error_message text,
  diagnostic jsonb,
  cost_metadata jsonb not null default '{}'::jsonb,
  started_at timestamptz,
  completed_at timestamptz,
  next_attempt_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- At most one active (queued/running) job per meeting.
create unique index processing_jobs_active_meeting_uidx
  on public.processing_jobs (meeting_id)
  where status in ('queued', 'running');
create index processing_jobs_status_idx
  on public.processing_jobs (status, next_attempt_at, created_at);
create index processing_jobs_meeting_idx on public.processing_jobs (meeting_id, created_at desc);

create trigger processing_jobs_updated_at
  before update on public.processing_jobs
  for each row execute function public.set_updated_at();

create table public.processing_job_events (
  id uuid primary key default gen_random_uuid(),
  job_id uuid not null references public.processing_jobs (id) on delete cascade,
  meeting_id uuid not null references public.meetings (id) on delete cascade,
  stage text not null,
  status text not null check (status in ('started', 'completed', 'failed', 'retrying', 'skipped')),
  attempt_number integer not null default 1,
  started_at timestamptz,
  completed_at timestamptz,
  duration_ms integer,
  error_code text,
  error_message text,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index processing_job_events_job_idx on public.processing_job_events (job_id, created_at);
create index processing_job_events_meeting_idx on public.processing_job_events (meeting_id, created_at);
