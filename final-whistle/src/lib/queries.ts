import { createServerSupabase } from './supabase/server';
import type { Match, Profile } from './types';

// Server-side data fetchers (respect RLS as the signed-in user).

export async function getProfile(): Promise<Profile | null> {
  const supabase = createServerSupabase();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return null;
  const { data } = await supabase.from('profiles').select('*').eq('id', user.id).single();
  return data as Profile | null;
}

/** Overall rank by XP: how many players have strictly more XP, +1. */
export async function getRank(xp: number): Promise<number> {
  const supabase = createServerSupabase();
  const { count } = await supabase
    .from('profiles')
    .select('id', { count: 'exact', head: true })
    .gt('xp', xp);
  return (count ?? 0) + 1;
}

/** Matches kicking off from the start of today onward, soonest first. */
export async function getUpcomingMatches(): Promise<Match[]> {
  const supabase = createServerSupabase();
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  const { data } = await supabase
    .from('matches')
    .select('*, home_team:home_team_id(*), away_team:away_team_id(*)')
    .gte('kickoff_at', start.toISOString())
    .order('kickoff_at', { ascending: true })
    .limit(20);
  return (data as Match[]) ?? [];
}

/** Set of match ids the current user has already predicted. */
export async function getPredictedMatchIds(): Promise<Set<string>> {
  const supabase = createServerSupabase();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return new Set();
  const { data } = await supabase.from('predictions').select('match_id').eq('user_id', user.id);
  return new Set((data ?? []).map((r) => r.match_id as string));
}
