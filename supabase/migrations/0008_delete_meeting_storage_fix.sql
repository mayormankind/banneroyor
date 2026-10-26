-- 0008: fix delete_meeting for hosted Supabase.
--
-- Hosted Supabase forbids direct DELETE on storage.objects ("Direct deletion
-- from storage tables is not allowed"). The web delete flow already removes
-- the underlying objects through the Storage API before calling this RPC, so
-- the storage row cleanup here was redundant as well as broken. The RPC now
-- deletes only the meeting row; dependent rows cascade. Orphaned blobs (if the
-- API-side removal failed) are reclaimed by periodic storage maintenance, not
-- by this function.

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

  delete from public.meetings where id = p_meeting_id;
end;
$$;

grant execute on function public.delete_meeting(uuid) to authenticated;
