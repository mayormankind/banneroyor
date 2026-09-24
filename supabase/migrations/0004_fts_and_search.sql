-- 0004: full-text search columns, indexes, and the search_meetings RPC

alter table public.meetings
  add column search_tsv tsvector
  generated always as (
    to_tsvector('english', coalesce(title, '') || ' ' || coalesce(description, ''))
  ) stored;

alter table public.transcript_segments
  add column text_tsv tsvector
  generated always as (
    to_tsvector('english', coalesce(user_text, text, ''))
  ) stored;

alter table public.summaries
  add column search_tsv tsvector
  generated always as (to_tsvector('english', coalesce(summary, ''))) stored;

alter table public.key_points
  add column search_tsv tsvector
  generated always as (to_tsvector('english', coalesce(content, ''))) stored;

alter table public.decisions
  add column search_tsv tsvector
  generated always as (to_tsvector('english', coalesce(description, ''))) stored;

alter table public.action_items
  add column search_tsv tsvector
  generated always as (to_tsvector('english', coalesce(description, ''))) stored;

alter table public.questions
  add column search_tsv tsvector
  generated always as (to_tsvector('english', coalesce(question, ''))) stored;

create index meetings_search_idx on public.meetings using gin (search_tsv);
create index transcript_segments_text_idx on public.transcript_segments using gin (text_tsv);
create index summaries_search_idx on public.summaries using gin (search_tsv);
create index key_points_search_idx on public.key_points using gin (search_tsv);
create index decisions_search_idx on public.decisions using gin (search_tsv);
create index action_items_search_idx on public.action_items using gin (search_tsv);
create index questions_search_idx on public.questions using gin (search_tsv);

-- Cross-meeting full-text search. Runs as the caller so RLS enforces ownership;
-- results are grouped client-side by meeting.
create or replace function public.search_meetings(
  p_query text,
  p_types text[] default null,
  p_date_from timestamptz default null,
  p_date_to timestamptz default null,
  p_participant text default null,
  p_limit integer default 50,
  p_offset integer default 0
)
returns table (
  meeting_id uuid,
  meeting_title text,
  meeting_date timestamptz,
  result_type text,
  result_id uuid,
  excerpt text,
  rank real,
  segment_start_ms bigint
)
language plpgsql
stable
security invoker
set search_path = public
as $$
declare
  v_tsquery tsquery := websearch_to_tsquery('english', coalesce(p_query, ''));
begin
  if p_query is null or btrim(p_query) = '' then
    return;
  end if;

  return query
  with owned_meetings as (
    select m.id, m.title, m.meeting_date
    from public.meetings m
    where m.owner_id = auth.uid()
      and (p_date_from is null or m.meeting_date >= p_date_from)
      and (p_date_to is null or m.meeting_date <= p_date_to)
      and (
        p_participant is null
        or exists (
          select 1 from public.participants p
          where p.meeting_id = m.id
            and p.display_name ilike '%' || p_participant || '%'
        )
      )
  ),
  hits as (
    select om.id as meeting_id, om.title, om.meeting_date,
           'meeting'::text as result_type, m.id as result_id,
           ts_headline('english', coalesce(m.title, '') || ' — ' || coalesce(m.description, ''),
                       v_tsquery, 'MaxFragments=2, MaxWords=30') as excerpt,
           ts_rank_cd(m.search_tsv, v_tsquery) as rank,
           null::bigint as segment_start_ms
    from owned_meetings om
    join public.meetings m on m.id = om.id
    where (p_types is null or 'meeting' = any (p_types))
      and m.search_tsv @@ v_tsquery

    union all
    select om.id, om.title, om.meeting_date, 'summary', s.id,
           ts_headline('english', s.summary, v_tsquery, 'MaxFragments=2, MaxWords=30'),
           ts_rank_cd(s.search_tsv, v_tsquery), null
    from owned_meetings om
    join public.summaries s on s.meeting_id = om.id
    where (p_types is null or 'summary' = any (p_types))
      and s.search_tsv @@ v_tsquery

    union all
    select om.id, om.title, om.meeting_date, 'key_point', k.id,
           ts_headline('english', k.content, v_tsquery, 'MaxFragments=2, MaxWords=30'),
           ts_rank_cd(k.search_tsv, v_tsquery), k.timestamp_ms
    from owned_meetings om
    join public.key_points k on k.meeting_id = om.id
    where (p_types is null or 'key_point' = any (p_types))
      and k.search_tsv @@ v_tsquery

    union all
    select om.id, om.title, om.meeting_date, 'decision', d.id,
           ts_headline('english', d.description, v_tsquery, 'MaxFragments=2, MaxWords=30'),
           ts_rank_cd(d.search_tsv, v_tsquery), d.timestamp_ms
    from owned_meetings om
    join public.decisions d on d.meeting_id = om.id
    where (p_types is null or 'decision' = any (p_types))
      and d.search_tsv @@ v_tsquery

    union all
    select om.id, om.title, om.meeting_date, 'action_item', a.id,
           ts_headline('english', a.description, v_tsquery, 'MaxFragments=2, MaxWords=30'),
           ts_rank_cd(a.search_tsv, v_tsquery), a.timestamp_ms
    from owned_meetings om
    join public.action_items a on a.meeting_id = om.id
    where (p_types is null or 'action_item' = any (p_types))
      and a.search_tsv @@ v_tsquery

    union all
    select om.id, om.title, om.meeting_date, 'question', q.id,
           ts_headline('english', q.question, v_tsquery, 'MaxFragments=2, MaxWords=30'),
           ts_rank_cd(q.search_tsv, v_tsquery), q.timestamp_ms
    from owned_meetings om
    join public.questions q on q.meeting_id = om.id
    where (p_types is null or 'question' = any (p_types))
      and q.search_tsv @@ v_tsquery

    union all
    select om.id, om.title, om.meeting_date, 'transcript', t.id,
           ts_headline('english', coalesce(t.user_text, t.text), v_tsquery,
                       'MaxFragments=2, MaxWords=30'),
           ts_rank_cd(t.text_tsv, v_tsquery), t.start_ms::bigint
    from owned_meetings om
    join public.transcript_segments t on t.meeting_id = om.id
    where (p_types is null or 'transcript' = any (p_types))
      and t.text_tsv @@ v_tsquery
  )
  select h.meeting_id, h.title, h.meeting_date, h.result_type, h.result_id,
         h.excerpt, h.rank, h.segment_start_ms
  from hits h
  order by h.rank desc, h.meeting_date desc
  limit p_limit offset p_offset;
end;
$$;

grant execute on function public.search_meetings(text, text[], timestamptz, timestamptz, text, integer, integer)
  to authenticated;
