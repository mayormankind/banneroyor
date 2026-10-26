-- 0009: capture sessions + widen meetings.source_type
--
-- A meeting is a logical entity; a capture session is one attempt at obtaining
-- its media. Uploads remain implicit sessions (source_type='upload', no row);
-- browser/desktop/native captures get a first-class capture_sessions row so
-- lifecycle, retries, and multiple attempts are tracked.

-- The previous constraint only allowed 'upload'. Browser capture needs a wider
-- set; reserve every provider source now so later milestones add no migration.
alter table public.meetings
  drop constraint meetings_source_type_check;
alter table public.meetings
  add constraint meetings_source_type_check
  check (source_type in ('upload', 'browser', 'desktop', 'google_meet', 'zoom', 'teams'));

create table public.capture_sessions (
  id uuid primary key default gen_random_uuid(),
  meeting_id uuid not null references public.meetings (id) on delete cascade,
  owner_id uuid not null references public.profiles (id) on delete cascade,
  source_type text not null check (
    source_type in ('upload', 'browser', 'desktop', 'google_meet', 'zoom', 'teams')
  ),
  status text not null default 'created' check (
    status in (
      'created', 'requesting_permission', 'capturing', 'paused',
      'stopped', 'uploading', 'uploaded', 'processing',
      'completed', 'failed', 'cancelled'
    )
  ),
  audio_format text,
  sample_rate integer,
  channel_count integer,
  storage_path text,
  error_message text,
  metadata jsonb not null default '{}'::jsonb,
  started_at timestamptz,
  ended_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index capture_sessions_meeting_idx on public.capture_sessions (meeting_id, created_at desc);
create index capture_sessions_owner_idx on public.capture_sessions (owner_id, created_at desc);

create trigger capture_sessions_updated_at
  before update on public.capture_sessions
  for each row execute function public.set_updated_at();

alter table public.capture_sessions enable row level security;

create policy capture_sessions_all_own on public.capture_sessions
  for all to authenticated
  using (owner_id = auth.uid()) with check (owner_id = auth.uid());
