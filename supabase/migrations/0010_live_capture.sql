-- 0010: near-live capture transcription
--
-- Browser capture streams 20s MediaRecorder parts to storage; each part row
-- triggers a 'capture_sync' queue message. The worker byte-concatenates parts
-- (reconstructing the single continuous container), slices the
-- not-yet-transcribed tail, and writes PROVISIONAL segments to
-- live_transcript_segments. The final report still comes from the batch
-- pipeline — live rows are never merged into transcript_segments.

-- Sync cursor + heartbeat on the session.
alter table public.capture_sessions
  add column transcribed_part_index integer not null default -1,
  add column transcribed_ms_end bigint not null default 0,
  add column last_part_at timestamptz;

create table public.capture_parts (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.capture_sessions (id) on delete cascade,
  meeting_id uuid not null references public.meetings (id) on delete cascade,
  owner_id uuid not null references public.profiles (id) on delete cascade,
  part_index integer not null,
  storage_path text not null,
  size_bytes bigint,
  mime_type text,
  status text not null default 'uploaded'
    check (status in ('uploaded', 'processed', 'failed')),
  created_at timestamptz not null default now(),
  unique (session_id, part_index)
);

create index capture_parts_session_idx
  on public.capture_parts (session_id, part_index);

alter table public.capture_parts enable row level security;

-- Users see their own parts; inserts happen via notify_capture_part.
create policy capture_parts_select_own on public.capture_parts
  for select to authenticated using (owner_id = auth.uid());
create policy capture_parts_insert_own on public.capture_parts
  for insert to authenticated with check (owner_id = auth.uid());
create policy capture_parts_update_own on public.capture_parts
  for update to authenticated using (owner_id = auth.uid())
  with check (owner_id = auth.uid());

-- Provisional transcript. run_part_index = first part index of the sync run
-- that produced the segment (delete+insert key for retry-safety).
create table public.live_transcript_segments (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.capture_sessions (id) on delete cascade,
  meeting_id uuid not null references public.meetings (id) on delete cascade,
  owner_id uuid not null references public.profiles (id) on delete cascade,
  run_part_index integer not null,
  segment_index integer not null,
  speaker_label text,
  start_ms bigint not null,
  end_ms bigint not null check (end_ms >= start_ms),
  text text not null,
  created_at timestamptz not null default now(),
  unique (session_id, run_part_index, segment_index)
);

create index live_segments_meeting_idx
  on public.live_transcript_segments (meeting_id, start_ms);
create index live_segments_session_idx
  on public.live_transcript_segments (session_id, run_part_index);

alter table public.live_transcript_segments enable row level security;

-- Read-only for users; the worker writes via service role.
create policy live_segments_select_own on public.live_transcript_segments
  for select to authenticated using (owner_id = auth.uid());

-- ---------------------------------------------------------------------------
-- notify_capture_part: record an uploaded part and enqueue a sync.
-- Idempotent on (session_id, part_index).
-- ---------------------------------------------------------------------------
create or replace function public.notify_capture_part(
  p_session_id uuid,
  p_part_index integer,
  p_storage_path text,
  p_size_bytes bigint,
  p_mime_type text
)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_session public.capture_sessions%rowtype;
begin
  select * into v_session
  from public.capture_sessions where id = p_session_id;
  if v_session.id is null then
    raise exception 'capture session not found';
  end if;
  if not public.is_service_role() and auth.uid() is distinct from v_session.owner_id then
    raise exception 'not authorized';
  end if;

  insert into public.capture_parts
    (session_id, meeting_id, owner_id, part_index, storage_path, size_bytes, mime_type)
  values
    (p_session_id, v_session.meeting_id, v_session.owner_id,
     p_part_index, p_storage_path, p_size_bytes, p_mime_type)
  on conflict (session_id, part_index) do nothing;

  update public.capture_sessions
    set last_part_at = now()
    where id = p_session_id;

  perform pgmq.send(
    'meeting-processing',
    jsonb_build_object(
      'job_type', 'capture_sync',
      'meeting_id', v_session.meeting_id,
      'capture_session_id', p_session_id
    )
  );
end;
$$;

grant execute on function public.notify_capture_part(uuid, integer, text, bigint, text)
  to authenticated, service_role;

-- ---------------------------------------------------------------------------
-- commit_capture_sync: atomically commit a sync run. Returns false when the
-- cursor moved (a concurrent sync already committed this run). Worker-only.
-- ---------------------------------------------------------------------------
create or replace function public.commit_capture_sync(
  p_session_id uuid,
  p_expected_cursor integer,
  p_new_cursor integer,
  p_new_ms_end bigint,
  p_segments jsonb
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_session public.capture_sessions%rowtype;
begin
  if not public.is_service_role() then
    raise exception 'not authorized';
  end if;

  select * into v_session
  from public.capture_sessions where id = p_session_id for update;
  if v_session.id is null then
    return false;
  end if;
  if v_session.transcribed_part_index <> p_expected_cursor then
    return false; -- another sync committed first; converge via next message
  end if;

  -- Idempotent: replace the run's segments (run key = expected + 1).
  delete from public.live_transcript_segments
  where session_id = p_session_id
    and run_part_index = p_expected_cursor + 1;

  insert into public.live_transcript_segments
    (session_id, meeting_id, owner_id, run_part_index, segment_index,
     speaker_label, start_ms, end_ms, text)
  select
    p_session_id, v_session.meeting_id, v_session.owner_id,
    p_expected_cursor + 1,
    (s->>'segment_index')::integer,
    s->>'speaker_label',
    (s->>'start_ms')::bigint,
    (s->>'end_ms')::bigint,
    s->>'text'
  from jsonb_array_elements(p_segments) s;

  update public.capture_parts
    set status = 'processed'
    where session_id = p_session_id
      and part_index > p_expected_cursor
      and part_index <= p_new_cursor
      and status = 'uploaded';

  update public.capture_sessions
    set transcribed_part_index = p_new_cursor,
        transcribed_ms_end = p_new_ms_end
    where id = p_session_id;

  return true;
end;
$$;

grant execute on function public.commit_capture_sync(uuid, integer, integer, bigint, jsonb)
  to service_role;
