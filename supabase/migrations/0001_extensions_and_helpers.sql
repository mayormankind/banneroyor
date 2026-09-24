-- 0001: extensions and shared helpers

create extension if not exists pgcrypto;
create extension if not exists pgmq;

-- updated_at maintenance
create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- Returns true when the caller is the Supabase service role (worker / server).
create or replace function public.is_service_role()
returns boolean
language sql
stable
as $$
  select coalesce(auth.jwt() ->> 'role', '') = 'service_role';
$$;
