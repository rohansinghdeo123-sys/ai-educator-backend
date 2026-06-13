-- =====================================================================
-- Final Whistle — demo seed data (run AFTER schema.sql)
-- 8 fictional teams + a sample matchday so the app looks alive.
-- Safe to run more than once (guards prevent duplicates).
-- All names are invented — no protected/real club marks.
-- =====================================================================

-- ── Teams ────────────────────────────────────────────────────────────
insert into teams (name, short_name, color)
select v.name, v.short_name, v.color
from (values
  ('Crimson Lions',  'CRL', '#FF5C5C'),
  ('Azure Falcons',  'AZF', '#4DA6FF'),
  ('Emerald Wolves', 'EMW', '#39FF8B'),
  ('Golden Stags',   'GST', '#FFD45E'),
  ('Silver Sharks',  'SLS', '#C0C8D4'),
  ('Violet Cobras',  'VLC', '#B57BFF'),
  ('Scarlet Titans', 'SCT', '#FF7A3C'),
  ('Onyx Ravens',    'ONR', '#8A93A6')
) as v(name, short_name, color)
where not exists (select 1 from teams t where t.name = v.name);

-- ── Sample matchday ──────────────────────────────────────────────────
-- Kickoffs are relative to "now" so they're always upcoming and predictable.
do $$
declare
  bonus jsonb := '[{"key":"b0","label":"Both teams to score"},
                   {"key":"b1","label":"A red card is shown"},
                   {"key":"b2","label":"A penalty is awarded"}]'::jsonb;
  fixtures text[][] := array[
    array['Crimson Lions','Azure Falcons','3 hours','Group Stage'],
    array['Emerald Wolves','Golden Stags','6 hours','Group Stage'],
    array['Silver Sharks','Violet Cobras','27 hours','Group Stage'],
    array['Scarlet Titans','Onyx Ravens','30 hours','Group Stage'],
    array['Crimson Lions','Emerald Wolves','2 days','Round of 16']
  ];
  f text[];
  hid uuid;
  aid uuid;
begin
  foreach f slice 1 in array fixtures loop
    select id into hid from teams where name = f[1];
    select id into aid from teams where name = f[2];
    if hid is null or aid is null then continue; end if;
    if not exists (
      select 1 from matches m
      where m.home_team_id = hid and m.away_team_id = aid and m.status = 'scheduled'
    ) then
      insert into matches (home_team_id, away_team_id, kickoff_at, stage, bonus_event_defs)
      values (hid, aid, now() + (f[3])::interval, f[4], bonus);
    end if;
  end loop;
end $$;
