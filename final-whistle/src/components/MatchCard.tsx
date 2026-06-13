import Link from 'next/link';
import type { Match } from '@/lib/types';
import { kickoffTime } from '@/lib/format';
import CountdownTimer from './CountdownTimer';

function Crest({ name, color }: { name: string; color: string }) {
  const initials = name
    .split(' ')
    .map((w) => w[0])
    .join('')
    .slice(0, 3)
    .toUpperCase();
  return (
    <div
      className="flex h-12 w-12 items-center justify-center rounded-xl font-display text-sm font-bold"
      style={{ background: `${color}22`, color, boxShadow: `0 0 14px ${color}55` }}
    >
      {initials}
    </div>
  );
}

export default function MatchCard({
  match,
  predicted,
}: {
  match: Match;
  predicted?: boolean;
}) {
  const home = match.home_team;
  const away = match.away_team;
  const settled = match.status === 'settled' || match.status === 'finished';

  return (
    <Link href={`/matches/${match.id}`} className="block">
      <div className="glass p-4 transition hover:border-neon/30 hover:shadow-neon">
        <div className="mb-3 flex items-center justify-between text-xs text-white/50">
          <span>{match.stage}</span>
          <span className="font-semibold text-white/70">{kickoffTime(match.kickoff_at)}</span>
        </div>

        <div className="flex items-center justify-between">
          <div className="flex flex-1 items-center gap-3">
            <Crest name={home?.name ?? '?'} color={home?.color ?? '#39FF8B'} />
            <span className="font-semibold">{home?.short_name ?? 'TBD'}</span>
          </div>

          <div className="px-3 text-center">
            {settled ? (
              <div className="font-display text-2xl font-bold">
                {match.home_score}–{match.away_score}
              </div>
            ) : (
              <div className="text-center">
                <div className="text-[10px] uppercase tracking-wider text-white/40">Kick-off</div>
                <CountdownTimer to={match.kickoff_at} className="font-display font-bold text-neon" />
              </div>
            )}
          </div>

          <div className="flex flex-1 items-center justify-end gap-3">
            <span className="font-semibold">{away?.short_name ?? 'TBD'}</span>
            <Crest name={away?.name ?? '?'} color={away?.color ?? '#FFD45E'} />
          </div>
        </div>

        <div className="pitch-line my-3" />
        <div className="flex items-center justify-between text-xs">
          {predicted ? (
            <span className="text-neon">✓ Prediction locked in</span>
          ) : settled ? (
            <span className="text-white/50">Result settled</span>
          ) : (
            <span className="text-gold">Tap to predict →</span>
          )}
        </div>
      </div>
    </Link>
  );
}
