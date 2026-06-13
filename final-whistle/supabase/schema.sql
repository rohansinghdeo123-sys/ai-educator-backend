-- =====================================================================
-- Final Whistle — Supabase schema (run in Supabase SQL editor)
-- Idempotent-ish: safe to re-run during development.
-- =====================================================================

-- ── Enums ────────────────────────────────────────────────────────────
do $$ begin
  create type match_status as enum ('scheduled','live','finished','settled');
exception when duplicate_object then null; end $$;

do $$ begin
  create type predicted_result as enum ('home','away','draw');
exception when duplicate_object then null; end $$;

do $$ begin
  create type confidence_level as enum ('low','medium','high');
exception when duplicate_object then null; end $$;

-- ── profiles (1:1 with auth.users) ───────────────────────────────────
create table if not exists profiles (
  id            uuid primary key references auth.users(id) on delete cascade,
  username      text unique,
  avatar_url    text,
  xp            integer not null default 0,
  coins         integer not null default 100,
  streak        integer not null default 0,
  best_streak   integer not null default 0,
  fan_title     text not null default 'Rookie Fan',
  avatar_frame  text not null default 'none',
  is_admin      boolean not null default false,
  created_at    timestamptz not null default now()
);

-- ── teams (fictional — no protected marks) ───────────────────────────
create table if not exists teams (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  short_name  text not null,
  crest_url   text,
  color       text not null default '#39FF8B',
  created_at  timestamptz not null default now()
);

-- ── matches ──────────────────────────────────────────────────────────
create table if not exists matches (
  id                   uuid primary key default gen_random_uuid(),
  home_team_id         uuid not null references teams(id),
  away_team_id         uuid not null references teams(id),
  kickoff_at           timestamptz not null,
  stage                text not null default 'Group Stage',
  status               match_status not null default 'scheduled',
  home_score           integer,
  away_score           integer,
  -- list of bonus events offered for this match, e.g.
  -- [{"key":"btts","label":"Both teams score"},{"key":"red","label":"Red card"}]
  bonus_event_defs     jsonb not null default '[]'::jsonb,
  -- actual results, e.g. {"btts": true, "red": false}
  bonus_event_results  jsonb not null default '{}'::jsonb,
  created_at           timestamptz not null default now()
);
create index if not exists matches_kickoff_idx on matches (kickoff_at);

-- ── predictions ──────────────────────────────────────────────────────
create table if not exists predictions (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references profiles(id) on delete cascade,
  match_id          uuid not null references matches(id) on delete cascade,
  predicted_winner  predicted_result not null,
  home_score        integer not null,
  away_score        integer not null,
  confidence        confidence_level not null default 'medium',
  -- user-picked bonus events: {"btts": true, "red": false}
  bonus_events      jsonb not null default '{}'::jsonb,
  xp_awarded        integer not null default 0,
  is_settled        boolean not null default false,
  created_at        timestamptz not null default now(),
  unique (user_id, match_id)
);

-- ── badges & rewards ─────────────────────────────────────────────────
create table if not exists badges (
  id          uuid primary key default gen_random_uuid(),
  code        text unique not null,
  name        text not null,
  description text not null,
  icon        text not null default '🏅'
);

create table if not exists user_badges (
  user_id    uuid not null references profiles(id) on delete cascade,
  badge_id   uuid not null references badges(id) on delete cascade,
  earned_at  timestamptz not null default now(),
  primary key (user_id, badge_id)
);

-- ── private leagues ──────────────────────────────────────────────────
create table if not exists leagues (
  id          uuid primary key default gen_random_uuid(),
  name        text not null,
  code        text unique not null,
  owner_id    uuid not null references profiles(id) on delete cascade,
  created_at  timestamptz not null default now()
);

create table if not exists league_members (
  league_id  uuid not null references leagues(id) on delete cascade,
  user_id    uuid not null references profiles(id) on delete cascade,
  joined_at  timestamptz not null default now(),
  primary key (league_id, user_id)
);

-- ── mini-game scores ─────────────────────────────────────────────────
create table if not exists shootout_scores (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references profiles(id) on delete cascade,
  score      integer not null,
  played_at  timestamptz not null default now()
);

-- ── coin ledger ──────────────────────────────────────────────────────
create table if not exists coin_transactions (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references profiles(id) on delete cascade,
  amount      integer not null,
  reason      text not null,
  created_at  timestamptz not null default now()
);

-- =====================================================================
-- Profile bootstrap: create a profile row when a user signs up
-- =====================================================================
create or replace function handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  insert into profiles (id, username, avatar_url)
  values (
    new.id,
    coalesce(new.raw_user_meta_data->>'full_name', split_part(new.email,'@',1)),
    new.raw_user_meta_data->>'avatar_url'
  )
  on conflict (id) do nothing;
  return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function handle_new_user();

-- =====================================================================
-- Helper: award coins + write ledger entry atomically
-- =====================================================================
create or replace function award_coins(p_user uuid, p_amount int, p_reason text)
returns void language plpgsql security definer set search_path = public as $$
begin
  update profiles set coins = coins + p_amount where id = p_user;
  insert into coin_transactions (user_id, amount, reason)
  values (p_user, p_amount, p_reason);
end $$;

-- =====================================================================
-- Row Level Security
-- =====================================================================
alter table profiles          enable row level security;
alter table teams             enable row level security;
alter table matches           enable row level security;
alter table predictions       enable row level security;
alter table badges            enable row level security;
alter table user_badges       enable row level security;
alter table leagues           enable row level security;
alter table league_members    enable row level security;
alter table shootout_scores   enable row level security;
alter table coin_transactions enable row level security;

-- profiles: everyone authed can read (leaderboards); only owner updates self
drop policy if exists profiles_read on profiles;
create policy profiles_read on profiles for select to authenticated using (true);
drop policy if exists profiles_update on profiles;
create policy profiles_update on profiles for update to authenticated
  using (auth.uid() = id) with check (auth.uid() = id);

-- teams & matches: read for all authed; writes handled by service role (admin API)
drop policy if exists teams_read on teams;
create policy teams_read on teams for select to authenticated using (true);
drop policy if exists matches_read on matches;
create policy matches_read on matches for select to authenticated using (true);

-- predictions: owner full read; insert own; update own only before settlement
drop policy if exists predictions_read on predictions;
create policy predictions_read on predictions for select to authenticated
  using (auth.uid() = user_id);
drop policy if exists predictions_insert on predictions;
create policy predictions_insert on predictions for insert to authenticated
  with check (auth.uid() = user_id);
drop policy if exists predictions_update on predictions;
create policy predictions_update on predictions for update to authenticated
  using (auth.uid() = user_id and is_settled = false)
  with check (auth.uid() = user_id and is_settled = false);

-- badges: read all; user_badges read all (for profile showcases)
drop policy if exists badges_read on badges;
create policy badges_read on badges for select to authenticated using (true);
drop policy if exists user_badges_read on user_badges;
create policy user_badges_read on user_badges for select to authenticated using (true);

-- leagues: members read; anyone authed can create; join via membership
drop policy if exists leagues_read on leagues;
create policy leagues_read on leagues for select to authenticated using (true);
drop policy if exists leagues_insert on leagues;
create policy leagues_insert on leagues for insert to authenticated
  with check (auth.uid() = owner_id);
drop policy if exists league_members_read on league_members;
create policy league_members_read on league_members for select to authenticated using (true);
drop policy if exists league_members_insert on league_members;
create policy league_members_insert on league_members for insert to authenticated
  with check (auth.uid() = user_id);

-- shootout: read all (leaderboard), insert own
drop policy if exists shootout_read on shootout_scores;
create policy shootout_read on shootout_scores for select to authenticated using (true);
drop policy if exists shootout_insert on shootout_scores;
create policy shootout_insert on shootout_scores for insert to authenticated
  with check (auth.uid() = user_id);

-- coin ledger: owner read only
drop policy if exists coins_read on coin_transactions;
create policy coins_read on coin_transactions for select to authenticated
  using (auth.uid() = user_id);

-- =====================================================================
-- Seed badges
-- =====================================================================
insert into badges (code, name, description, icon) values
  ('first_pred',  'First Whistle',   'Make your first prediction',          '🎯'),
  ('streak_3',    'On a Roll',       'Reach a 3-match streak',              '🔥'),
  ('streak_10',   'Unstoppable',     'Reach a 10-match streak',             '⚡'),
  ('exact_score', 'Crystal Ball',    'Predict an exact scoreline',          '🔮'),
  ('shootout_5',  'Spot-Kick Hero',  'Score 5 in the penalty shootout',     '🥅')
on conflict (code) do nothing;
