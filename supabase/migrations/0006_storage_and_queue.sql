-- 0006: private storage bucket, storage policies, PGMQ queue and queue RPCs

-- ---------------------------------------------------------------------------
-- Storage: private bucket for recordings. Object layout:
--   {owner_id}/{meeting_id}/{file}
-- Users can only read/write inside their own top-level folder.
-- ---------------------------------------------------------------------------

insert into storage.buckets (id, name, public)
values ('meeting-recordings', 'meeting-recordings', false)
on conflict (id) do nothing;

create policy recordings_select_own on storage.objects
  for select to authenticated
  using (
    bucket_id = 'meeting-recordings'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

create policy recordings_insert_own on storage.objects
  for insert to authenticated
  with check (
    bucket_id = 'meeting-recordings'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

create policy recordings_update_own on storage.objects
  for update to authenticated
  using (
    bucket_id = 'meeting-recordings'
    and (storage.foldername(name))[1] = auth.uid()::text
  )
  with check (
    bucket_id = 'meeting-recordings'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

create policy recordings_delete_own on storage.objects
  for delete to authenticated
  using (
    bucket_id = 'meeting-recordings'
    and (storage.foldername(name))[1] = auth.uid()::text
  );

-- ---------------------------------------------------------------------------
-- Queue: PGMQ-backed durable queue. All access goes through the RPC wrappers
-- below so the queue implementation stays behind an adapter boundary.
-- ---------------------------------------------------------------------------

do $$
begin
  if not exists (select 1 from pgmq.list_queues() where queue_name = 'meeting-processing') then
    perform pgmq.create('meeting-processing');
  end if;
end $$;

-- Enqueue a processing message. Callable by the meeting owner (web) or the
-- service role (retry paths). The message carries identifiers only.
create or replace function public.enqueue_meeting_job(
  p_meeting_id uuid,
  p_job_id uuid,
  p_job_type text default 'process_meeting',
  p_delay_seconds integer default 0
)
returns bigint
language plpgsql
security definer
set search_path = public
as $$
declare
  v_owner uuid;
  v_msg_id bigint;
begin
  select owner_id into v_owner from public.meetings where id = p_meeting_id;
  if v_owner is null then
    raise exception 'meeting not found';
  end if;
  if not public.is_service_role() and auth.uid() is distinct from v_owner then
    raise exception 'not authorized';
  end if;
  select pgmq.send(
    'meeting-processing',
    jsonb_build_object(
      'meeting_id', p_meeting_id,
      'job_id', p_job_id,
      'job_type', p_job_type
    ),
    greatest(p_delay_seconds, 0)
  ) into v_msg_id;
  return v_msg_id;
end;
$$;

-- Worker-facing queue operations: service role only.
create or replace function public.read_meeting_jobs(
  p_vt_seconds integer default 900,
  p_qty integer default 1
)
returns table (
  msg_id bigint,
  read_ct integer,
  enqueued_at timestamptz,
  vt timestamptz,
  message jsonb
)
language plpgsql
security definer
set search_path = public
as $$
begin
  if not public.is_service_role() then
    raise exception 'not authorized';
  end if;
  return query
    select r.msg_id, r.read_ct, r.enqueued_at, r.vt, r.message
    from pgmq.read('meeting-processing', p_vt_seconds, p_qty) r;
end;
$$;

create or replace function public.delete_meeting_job_message(p_msg_id bigint)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
  if not public.is_service_role() then
    raise exception 'not authorized';
  end if;
  return pgmq.delete('meeting-processing', p_msg_id);
end;
$$;

create or replace function public.archive_meeting_job_message(p_msg_id bigint)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
  if not public.is_service_role() then
    raise exception 'not authorized';
  end if;
  return pgmq.archive('meeting-processing', p_msg_id);
end;
$$;

-- Postpone a message (nack) by moving its visibility timeout forward.
create or replace function public.postpone_meeting_job_message(
  p_msg_id bigint,
  p_delay_seconds integer
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if not public.is_service_role() then
    raise exception 'not authorized';
  end if;
  perform pgmq.set_vt(
    'meeting-processing',
    p_msg_id,
    now() + make_interval(secs => greatest(p_delay_seconds, 0))
  );
end;
$$;

grant execute on function public.enqueue_meeting_job(uuid, uuid, text, integer)
  to authenticated, service_role;
grant execute on function public.read_meeting_jobs(integer, integer) to service_role;
grant execute on function public.delete_meeting_job_message(bigint) to service_role;
grant execute on function public.archive_meeting_job_message(bigint) to service_role;
grant execute on function public.postpone_meeting_job_message(bigint, integer) to service_role;
