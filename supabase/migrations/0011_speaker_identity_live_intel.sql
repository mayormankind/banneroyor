-- 0011: speaker identity + live intelligence
--
-- Speaker identity: when the transcription provider confirms a known speaker
-- (voice reference match), the worker now links the meeting participant and
-- its segments to the speaker_profiles row, so corrections and enrollments
-- accumulate on a durable identity rather than a per-meeting label.
--
-- Live intelligence: during a capture session the worker periodically analyses
-- the provisional transcript and writes CANDIDATE items to
-- live_intelligence_items. These are wholesale-replaced on each run and never
-- merged into the authoritative decisions/action_items tables.

-- Link a meeting participant to a known-speaker profile.
alter table public.participants
  add column speaker_profile_id uuid references public.speaker_profiles (id) on delete set null;

create index participants_speaker_profile_idx
  on public.participants (speaker_profile_id);

-- Live-intelligence cursor on the session: the last part index for which a
-- candidate-intelligence run was committed. Throttles LLM calls.
alter table public.capture_sessions
  add column intel_part_index integer not null default -1;

-- Provisional intelligence items. One live "run" replaces the whole set for
-- the session — candidates are cumulative, not append-only.
create table public.live_intelligence_items (
  id uuid primary key default gen_random_uuid(),
  session_id uuid not null references public.capture_sessions (id) on delete cascade,
  meeting_id uuid not null references public.meetings (id) on delete cascade,
  owner_id uuid not null references public.profiles (id) on delete cascade,
  run_part_index integer not null,
  item_index integer not null,
  kind text not null check (kind in ('key_point', 'decision', 'action_item', 'question')),
  content text not null,
  speaker_label text,
  timestamp_ms bigint,
  evidence_segment_indices integer[] not null default '{}',
  created_at timestamptz not null default now(),
  unique (session_id, run_part_index, item_index)
);

create index live_intel_meeting_idx
  on public.live_intelligence_items (meeting_id, run_part_index);

alter table public.live_intelligence_items enable row level security;

-- Read-only for users; the worker writes via service role.
create policy live_intel_select_own on public.live_intelligence_items
  for select to authenticated using (owner_id = auth.uid());

-- ---------------------------------------------------------------------------
-- commit_capture_intel: atomically replace a session's live-intelligence set.
-- Guarded on intel_part_index so concurrent syncs can't clobber each other.
-- Worker-only.
-- ---------------------------------------------------------------------------
create or replace function public.commit_capture_intel(
  p_session_id uuid,
  p_expected_cursor integer,
  p_new_cursor integer,
  p_items jsonb
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
  if v_session.intel_part_index <> p_expected_cursor then
    return false; -- another intel run committed first
  end if;

  -- The candidate set is cumulative: a newer run supersedes all prior items.
  delete from public.live_intelligence_items
  where session_id = p_session_id;

  insert into public.live_intelligence_items
    (session_id, meeting_id, owner_id, run_part_index, item_index,
     kind, content, speaker_label, timestamp_ms, evidence_segment_indices)
  select
    p_session_id, v_session.meeting_id, v_session.owner_id,
    p_new_cursor,
    (s->>'item_index')::integer,
    s->>'kind',
    s->>'content',
    s->>'speaker_label',
    (s->>'timestamp_ms')::bigint,
    coalesce(
      (select array_agg(e::integer)
       from jsonb_array_elements_text(s->'evidence_segment_indices') e),
      '{}'::integer[]
    )
  from jsonb_array_elements(p_items) s;

  update public.capture_sessions
    set intel_part_index = p_new_cursor
    where id = p_session_id;

  return true;
end;
$$;

grant execute on function public.commit_capture_intel(uuid, integer, integer, jsonb)
  to service_role;
