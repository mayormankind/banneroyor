-- 0012: fix postpone_meeting_job_message for hosted pgmq
--
-- 0006 called pgmq.set_vt(queue, msg_id, timestamptz). The pgmq version on
-- hosted Supabase only exposes the integer-offset overload
-- set_vt(queue, msg_id, vt_offset_seconds), so every postpone failed with
-- 42883 — discovered when a transient network error hit the retry path.

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
    greatest(p_delay_seconds, 0)
  );
end;
$$;

grant execute on function public.postpone_meeting_job_message(bigint, integer) to service_role;
