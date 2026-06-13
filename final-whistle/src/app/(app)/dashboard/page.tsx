import Link from 'next/link';
import StatBar from '@/components/StatBar';
import MatchCard from '@/components/MatchCard';
import CountdownTimer from '@/components/CountdownTimer';
import { getProfile, getRank, getUpcomingMatches, getPredictedMatchIds } from '@/lib/queries';

export const dynamic = 'force-dynamic';

export default async function DashboardPage() {
  const profile = await getProfile();
  if (!profile) return null;

  const [rank, matches, predictedIds] = await Promise.all([
    getRank(profile.xp),
    getUpcomingMatches(),
    getPredictedMatchIds(),
  ]);

  const nextMatch = matches.find((m) => new Date(m.kickoff_at).getTime() > Date.now());

  return (
    <div className="space-y-6">
      <div>
        <h1 className="font-display text-2xl font-bold">
          Welcome back{profile.username ? `, ${profile.username.split(' ')[0]}` : ''} 👋
        </h1>
        <p className="text-sm text-white/50">{profile.fan_title}</p>
      </div>

      <StatBar profile={profile} rank={rank} />

      {/* Next match countdown hero */}
      {nextMatch && (
        <Link href={`/matches/${nextMatch.id}`}>
          <div className="glass relative overflow-hidden p-5">
            <div className="pointer-events-none absolute inset-0 bg-pitch-lines" />
            <div className="relative">
              <div className="text-xs uppercase tracking-widest text-white/50">Next kick-off in</div>
              <CountdownTimer
                to={nextMatch.kickoff_at}
                className="font-display text-4xl font-extrabold text-neon drop-shadow-[0_0_18px_rgba(57,255,139,0.4)]"
              />
              <div className="mt-2 text-sm text-white/70">
                {nextMatch.home_team?.name} vs {nextMatch.away_team?.name}
              </div>
            </div>
          </div>
        </Link>
      )}

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="font-display text-lg font-bold">Today&apos;s Matches</h2>
          <Link href="/play" className="text-xs text-gold">
            🥅 Penalty game →
          </Link>
        </div>

        {matches.length === 0 ? (
          <div className="glass p-8 text-center text-white/50">
            No matches scheduled yet. Check back soon!
          </div>
        ) : (
          <div className="space-y-3">
            {matches.map((m) => (
              <MatchCard key={m.id} match={m} predicted={predictedIds.has(m.id)} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
