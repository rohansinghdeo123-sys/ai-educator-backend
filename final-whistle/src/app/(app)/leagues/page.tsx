import { createServerSupabase } from '@/lib/supabase/server';
import LeagueForms from '@/components/LeagueForms';

export const dynamic = 'force-dynamic';

interface LeagueView {
  id: string;
  name: string;
  code: string;
  members: { id: string; username: string | null; xp: number }[];
}

export default async function LeaguesPage() {
  const supabase = createServerSupabase();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  // Leagues I belong to.
  const { data: memberships } = await supabase
    .from('league_members')
    .select('league:league_id(id, name, code)')
    .eq('user_id', user!.id);

  const leagues = (memberships ?? [])
    .map((m) => (m as unknown as { league: { id: string; name: string; code: string } }).league)
    .filter(Boolean);

  // Load ranked members for each league.
  const views: LeagueView[] = [];
  for (const lg of leagues) {
    const { data: mem } = await supabase
      .from('league_members')
      .select('profile:user_id(id, username, xp)')
      .eq('league_id', lg.id);
    const members = (mem ?? [])
      .map((r) => (r as unknown as { profile: { id: string; username: string | null; xp: number } }).profile)
      .filter(Boolean)
      .sort((a, b) => b.xp - a.xp);
    views.push({ ...lg, members });
  }

  return (
    <div className="space-y-6">
      <h1 className="font-display text-2xl font-bold">👥 Private Leagues</h1>
      <LeagueForms />

      <div className="space-y-4">
        {views.length === 0 ? (
          <div className="glass p-8 text-center text-white/50">
            You&apos;re not in any leagues yet. Create one and share the code with friends!
          </div>
        ) : (
          views.map((lg) => (
            <div key={lg.id} className="glass p-4">
              <div className="mb-3 flex items-center justify-between">
                <h2 className="font-display font-bold">{lg.name}</h2>
                <span className="stat-pill text-gold">Code: {lg.code}</span>
              </div>
              <div className="space-y-1">
                {lg.members.map((mb, i) => (
                  <div
                    key={mb.id}
                    className={`flex items-center justify-between rounded-lg px-3 py-2 text-sm ${
                      mb.id === user?.id ? 'bg-neon/10 text-neon' : 'bg-white/5'
                    }`}
                  >
                    <span>
                      {i + 1}. {mb.username ?? 'Player'}
                    </span>
                    <span className="font-semibold">{mb.xp.toLocaleString()} XP</span>
                  </div>
                ))}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
