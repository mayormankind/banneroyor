-- 0005: row level security and column-level edit restrictions
--
-- Every user-owned table is owner-scoped via owner_id = auth.uid() (or profile id).
-- The service role bypasses RLS entirely, so the worker is unconstrained.

alter table public.profiles enable row level security;
alter table public.meetings enable row level security;
alter table public.participants enable row level security;
alter table public.speaker_profiles enable row level security;
alter table public.transcript_segments enable row level security;
alter table public.summaries enable row level security;
alter table public.key_points enable row level security;
alter table public.decisions enable row level security;
alter table public.action_items enable row level security;
alter table public.questions enable row level security;
alter table public.processing_jobs enable row level security;
alter table public.processing_job_events enable row level security;

create policy profiles_select_own on public.profiles
  for select to authenticated using (id = auth.uid());
create policy profiles_update_own on public.profiles
  for update to authenticated using (id = auth.uid()) with check (id = auth.uid());

create policy meetings_select_own on public.meetings
  for select to authenticated using (owner_id = auth.uid());
create policy meetings_insert_own on public.meetings
  for insert to authenticated with check (owner_id = auth.uid());
create policy meetings_update_own on public.meetings
  for update to authenticated using (owner_id = auth.uid()) with check (owner_id = auth.uid());
create policy meetings_delete_own on public.meetings
  for delete to authenticated using (owner_id = auth.uid());

create policy participants_all_own on public.participants
  for all to authenticated
  using (owner_id = auth.uid()) with check (owner_id = auth.uid());

create policy speaker_profiles_all_own on public.speaker_profiles
  for all to authenticated
  using (owner_id = auth.uid()) with check (owner_id = auth.uid());

create policy transcript_segments_select_own on public.transcript_segments
  for select to authenticated using (owner_id = auth.uid());
create policy transcript_segments_update_own on public.transcript_segments
  for update to authenticated using (owner_id = auth.uid()) with check (owner_id = auth.uid());
-- No direct insert/delete for users: segments are written by the worker only.

create policy summaries_select_own on public.summaries
  for select to authenticated using (owner_id = auth.uid());
create policy summaries_update_own on public.summaries
  for update to authenticated using (owner_id = auth.uid()) with check (owner_id = auth.uid());

create policy key_points_select_own on public.key_points
  for select to authenticated using (owner_id = auth.uid());
create policy key_points_update_own on public.key_points
  for update to authenticated using (owner_id = auth.uid()) with check (owner_id = auth.uid());

create policy decisions_select_own on public.decisions
  for select to authenticated using (owner_id = auth.uid());
create policy decisions_update_own on public.decisions
  for update to authenticated using (owner_id = auth.uid()) with check (owner_id = auth.uid());

create policy action_items_select_own on public.action_items
  for select to authenticated using (owner_id = auth.uid());
create policy action_items_update_own on public.action_items
  for update to authenticated using (owner_id = auth.uid()) with check (owner_id = auth.uid());

create policy questions_select_own on public.questions
  for select to authenticated using (owner_id = auth.uid());
create policy questions_update_own on public.questions
  for update to authenticated using (owner_id = auth.uid()) with check (owner_id = auth.uid());

-- Job bookkeeping is read-only for users; writes come from RPCs or the worker.
create policy processing_jobs_select_own on public.processing_jobs
  for select to authenticated using (owner_id = auth.uid());
create policy processing_job_events_select_own on public.processing_job_events
  for select to authenticated using (
    exists (
      select 1 from public.meetings m
      where m.id = processing_job_events.meeting_id and m.owner_id = auth.uid()
    )
  );

-- Column-level update restrictions for end users. Users may correct display
-- fields but can never rewrite provider output, provenance, or ownership.
revoke update on public.transcript_segments from authenticated;
grant update (user_text, participant_id, speaker_profile_id, edited_at)
  on public.transcript_segments to authenticated;

revoke update on public.summaries from authenticated;
grant update (summary, is_edited) on public.summaries to authenticated;

revoke update on public.key_points from authenticated;
grant update (content, is_edited) on public.key_points to authenticated;

revoke update on public.decisions from authenticated;
grant update (description, participant_id, is_edited) on public.decisions to authenticated;

revoke update on public.action_items from authenticated;
grant update (description, owner_participant_id, due_date, status, is_edited)
  on public.action_items to authenticated;

revoke update on public.questions from authenticated;
grant update (question, resolved, is_edited) on public.questions to authenticated;

-- Meetings: users edit only the descriptive fields. The status-transition and
-- pipeline-field triggers additionally protect pipeline columns.
revoke update on public.meetings from authenticated;
grant update (title, description, meeting_date) on public.meetings to authenticated;
