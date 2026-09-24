-- 0002: core tables — profiles, meetings, participants, speaker_profiles, transcript_segments

create table public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  display_name text not null default '',
  email text,
  avatar_url text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger profiles_updated_at
  before update on public.profiles
  for each row execute function public.set_updated_at();

-- Auto-provision a profile when a user signs up.
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.profiles (id, display_name, email, avatar_url)
  values (
    new.id,
    coalesce(new.raw_user_meta_data ->> 'display_name', new.raw_user_meta_data ->> 'full_name', ''),
    new.email,
    new.raw_user_meta_data ->> 'avatar_url'
  )
  on conflict (id) do nothing;
  return new;
end;
$$;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

create table public.meetings (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references public.profiles (id) on delete cascade,
  title text not null check (char_length(title) between 1 and 300),
  description text,
  meeting_date timestamptz not null default now(),
  duration_seconds integer check (duration_seconds is null or duration_seconds >= 0),
  status text not null default 'draft' check (
    status in ('draft', 'uploading', 'uploaded', 'queued', 'processing', 'completed', 'failed', 'deleting')
  ),
  source_type text not null default 'upload' check (source_type in ('upload')),
  source_file_path text,
  source_file_name text,
  source_mime_type text,
  source_size_bytes bigint check (source_size_bytes is null or source_size_bytes >= 0),
  recording_consent_confirmed_at timestamptz,
  current_run_id uuid,
  failure_reason text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index meetings_owner_date_idx on public.meetings (owner_id, meeting_date desc);
create index meetings_owner_status_idx on public.meetings (owner_id, status);

create trigger meetings_updated_at
  before update on public.meetings
  for each row execute function public.set_updated_at();

-- Valid meeting status transitions. Pipeline fields are only writable by the
-- service role (the worker); user-driven transitions happen through RPCs that
-- run with elevated privileges after checking ownership.
create or replace function public.enforce_meeting_status_transition()
returns trigger
language plpgsql
as $$
declare
  allowed text[];
begin
  if new.status is not distinct from old.status then
    return new;
  end if;

  allowed := case old.status
    when 'draft'      then array['uploading', 'deleting']
    when 'uploading'  then array['uploaded', 'draft', 'deleting']
    when 'uploaded'   then array['queued', 'deleting']
    when 'queued'     then array['processing', 'failed', 'deleting']
    when 'processing' then array['completed', 'failed', 'queued', 'deleting']
    when 'completed'  then array['queued', 'deleting']
    when 'failed'     then array['queued', 'deleting']
    when 'deleting'   then array['failed']
    else array[]::text[]
  end;

  if not (new.status = any (allowed)) then
    raise exception 'invalid meeting status transition: % -> %', old.status, new.status
      using errcode = 'check_violation';
  end if;
  return new;
end;
$$;

create trigger meetings_status_transition
  before update of status on public.meetings
  for each row execute function public.enforce_meeting_status_transition();

-- Non-service callers may not touch pipeline-owned columns at all. Privileged
-- RPCs set the transaction-scoped flag 'banner_recall.internal' to pass.
create or replace function public.protect_meeting_pipeline_fields()
returns trigger
language plpgsql
as $$
begin
  if public.is_service_role()
     or current_setting('banner_recall.internal', true) = 'on' then
    return new;
  end if;
  if new.status is distinct from old.status
     or new.owner_id is distinct from old.owner_id
     or new.source_file_path is distinct from old.source_file_path
     or new.source_file_name is distinct from old.source_file_name
     or new.source_mime_type is distinct from old.source_mime_type
     or new.source_size_bytes is distinct from old.source_size_bytes
     or new.duration_seconds is distinct from old.duration_seconds
     or new.current_run_id is distinct from old.current_run_id
     or new.failure_reason is distinct from old.failure_reason then
    raise exception 'meeting pipeline fields are read-only';
  end if;
  return new;
end;
$$;

create trigger meetings_protect_pipeline_fields
  before update on public.meetings
  for each row execute function public.protect_meeting_pipeline_fields();

create table public.participants (
  id uuid primary key default gen_random_uuid(),
  meeting_id uuid not null references public.meetings (id) on delete cascade,
  owner_id uuid not null references public.profiles (id) on delete cascade,
  profile_id uuid references public.profiles (id) on delete set null,
  display_name text not null check (char_length(display_name) between 1 and 200),
  email text,
  speaker_label text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index participants_meeting_idx on public.participants (meeting_id);
-- A speaker label maps to at most one participant per meeting.
create unique index participants_meeting_speaker_uidx
  on public.participants (meeting_id, speaker_label)
  where speaker_label is not null;

create trigger participants_updated_at
  before update on public.participants
  for each row execute function public.set_updated_at();

create table public.speaker_profiles (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references public.profiles (id) on delete cascade,
  display_name text not null check (char_length(display_name) between 1 and 200),
  reference_storage_path text,
  reference_metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index speaker_profiles_owner_idx on public.speaker_profiles (owner_id);

create trigger speaker_profiles_updated_at
  before update on public.speaker_profiles
  for each row execute function public.set_updated_at();

create table public.transcript_segments (
  id uuid primary key default gen_random_uuid(),
  meeting_id uuid not null references public.meetings (id) on delete cascade,
  owner_id uuid not null references public.profiles (id) on delete cascade,
  run_id uuid,
  segment_index integer not null,
  speaker_label text,
  participant_id uuid references public.participants (id) on delete set null,
  speaker_profile_id uuid references public.speaker_profiles (id) on delete set null,
  start_ms integer not null check (start_ms >= 0),
  end_ms integer not null check (end_ms >= start_ms),
  text text not null,
  user_text text,
  confidence real check (confidence is null or (confidence >= 0 and confidence <= 1)),
  source text not null default 'provider',
  edited_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (meeting_id, run_id, segment_index)
);

create index transcript_segments_meeting_time_idx
  on public.transcript_segments (meeting_id, start_ms);
create index transcript_segments_meeting_speaker_idx
  on public.transcript_segments (meeting_id, speaker_label);

create trigger transcript_segments_updated_at
  before update on public.transcript_segments
  for each row execute function public.set_updated_at();
