-- 0007: application RPCs used by the web app

-- Finalize an upload: record the source object, create the processing job and
-- enqueue it, and move the meeting to 'queued' — all in one transaction.
-- Safe to call again after a network failure: if an active job already exists
-- it is returned instead of creating a duplicate.
create or replace function public.finalize_meeting_upload(
  p_meeting_id uuid,
  p_file_path text,
  p_file_name text,
  p_mime_type text,
  p_size_bytes bigint,
  p_max_attempts integer default 4
)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_meeting public.meetings%rowtype;
  v_job_id uuid;
  v_run_id uuid;
begin
  perform set_config('banner_recall.internal', 'on', true);

  select * into v_meeting from public.meetings where id = p_meeting_id for update;
  if v_meeting.id is null then
    raise exception 'meeting not found';
  end if;
  if auth.uid() is distinct from v_meeting.owner_id then
    raise exception 'not authorized';
  end if;

  -- Idempotent retry: an active job already covers this upload.
  select id into v_job_id
  from public.processing_jobs
  where meeting_id = p_meeting_id and status in ('queued', 'running')
  limit 1;
  if v_job_id is not null then
    return v_job_id;
  end if;

  if v_meeting.status not in ('draft', 'uploading', 'uploaded') then
    raise exception 'meeting is not awaiting upload (status: %)', v_meeting.status;
  end if;

  if not exists (
    select 1 from storage.objects
    where bucket_id = 'meeting-recordings' and name = p_file_path
  ) then
    raise exception 'uploaded file not found in storage';
  end if;

  -- Upload complete (step through 'uploading' when called straight from draft).
  if v_meeting.status = 'draft' then
    update public.meetings set status = 'uploading' where id = p_meeting_id;
  end if;

  update public.meetings
  set status = 'uploaded',
      source_file_path = p_file_path,
      source_file_name = p_file_name,
      source_mime_type = p_mime_type,
      source_size_bytes = p_size_bytes
  where id = p_meeting_id;

  v_job_id := gen_random_uuid();
  v_run_id := gen_random_uuid();

  insert into public.processing_jobs (
    id, meeting_id, owner_id, job_type, status, max_attempts, run_id, idempotency_key
  ) values (
    v_job_id, p_meeting_id, v_meeting.owner_id, 'process_meeting', 'queued',
    greatest(p_max_attempts, 1), v_run_id,
    'process:' || p_meeting_id::text || ':' || v_run_id::text
  );

  update public.meetings set status = 'queued' where id = p_meeting_id;

  perform public.enqueue_meeting_job(p_meeting_id, v_job_id, 'process_meeting', 0);

  return v_job_id;
end;
$$;

-- Requeue a failed (or completed) meeting for processing. Idempotent: returns
-- the existing active job when one exists.
create or replace function public.request_meeting_retry(p_meeting_id uuid)
returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_meeting public.meetings%rowtype;
  v_job_id uuid;
begin
  perform set_config('banner_recall.internal', 'on', true);

  select * into v_meeting from public.meetings where id = p_meeting_id for update;
  if v_meeting.id is null then
    raise exception 'meeting not found';
  end if;
  if auth.uid() is distinct from v_meeting.owner_id then
    raise exception 'not authorized';
  end if;

  select id into v_job_id
  from public.processing_jobs
  where meeting_id = p_meeting_id and status in ('queued', 'running')
  limit 1;
  if v_job_id is not null then
    return v_job_id;
  end if;

  if v_meeting.status not in ('failed', 'completed', 'uploaded') then
    raise exception 'meeting cannot be retried from status %', v_meeting.status;
  end if;
  if v_meeting.source_file_path is null then
    raise exception 'meeting has no uploaded recording';
  end if;

  v_job_id := gen_random_uuid();
  insert into public.processing_jobs (
    id, meeting_id, owner_id, job_type, status, max_attempts, run_id, idempotency_key
  ) values (
    v_job_id, p_meeting_id, v_meeting.owner_id, 'process_meeting', 'queued',
    4, gen_random_uuid(),
    'process:' || p_meeting_id::text || ':' || gen_random_uuid()::text
  );

  update public.meetings
  set status = 'queued', failure_reason = null
  where id = p_meeting_id;

  perform public.enqueue_meeting_job(p_meeting_id, v_job_id, 'process_meeting', 0);

  return v_job_id;
end;
$$;

-- Delete a meeting and every dependent record, plus storage object rows for
-- the meeting folder. Runs atomically; on failure nothing changes. The caller
-- should also remove the underlying storage objects via the storage API
-- (best-effort) since direct row deletion does not reclaim blob bytes on every
-- storage backend version.
create or replace function public.delete_meeting(p_meeting_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_owner uuid;
begin
  perform set_config('banner_recall.internal', 'on', true);

  select owner_id into v_owner from public.meetings where id = p_meeting_id;
  if v_owner is null then
    return; -- already gone; deletion is idempotent
  end if;
  if auth.uid() is distinct from v_owner then
    raise exception 'not authorized';
  end if;

  delete from storage.objects
  where bucket_id = 'meeting-recordings'
    and name like v_owner::text || '/' || p_meeting_id::text || '/%';

  delete from public.meetings where id = p_meeting_id;
end;
$$;

-- Move a draft meeting into 'uploading' so a direct storage upload can begin.
create or replace function public.begin_meeting_upload(p_meeting_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_owner uuid;
  v_status text;
begin
  perform set_config('banner_recall.internal', 'on', true);

  select owner_id, status into v_owner, v_status
  from public.meetings where id = p_meeting_id for update;
  if v_owner is null then
    raise exception 'meeting not found';
  end if;
  if auth.uid() is distinct from v_owner then
    raise exception 'not authorized';
  end if;
  if v_status <> 'draft' then
    raise exception 'meeting is not in draft state (status: %)', v_status;
  end if;

  update public.meetings set status = 'uploading' where id = p_meeting_id;
end;
$$;

-- Abort an in-progress upload and return the meeting to 'draft'.
create or replace function public.cancel_meeting_upload(p_meeting_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
  v_owner uuid;
  v_status text;
begin
  perform set_config('banner_recall.internal', 'on', true);

  select owner_id, status into v_owner, v_status
  from public.meetings where id = p_meeting_id for update;
  if v_owner is null then
    raise exception 'meeting not found';
  end if;
  if auth.uid() is distinct from v_owner then
    raise exception 'not authorized';
  end if;
  if v_status = 'uploading' then
    update public.meetings set status = 'draft' where id = p_meeting_id;
  end if;
end;
$$;

grant execute on function public.begin_meeting_upload(uuid) to authenticated;
grant execute on function public.cancel_meeting_upload(uuid) to authenticated;
grant execute on function public.finalize_meeting_upload(uuid, text, text, text, bigint, integer)
  to authenticated;
grant execute on function public.request_meeting_retry(uuid) to authenticated;
grant execute on function public.delete_meeting(uuid) to authenticated;
