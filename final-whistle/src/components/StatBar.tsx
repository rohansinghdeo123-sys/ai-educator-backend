import type { Profile } from '@/lib/types';
import { ordinal } from '@/lib/format';

export default function StatBar({ profile, rank }: { profile: Profile; rank: number | null }) {
  const stats = [
    { icon: '⚡', label: 'XP', value: profile.xp.toLocaleString() },
    { icon: '🪙', label: 'Coins', value: profile.coins.toLocaleString() },
    { icon: '🔥', label: 'Streak', value: String(profile.streak) },
    { icon: '🏆', label: 'Rank', value: rank ? ordinal(rank) : '—' },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {stats.map((s) => (
        <div key={s.label} className="glass flex flex-col items-center px-3 py-3">
          <span className="text-xl">{s.icon}</span>
          <span className="mt-1 font-display text-xl font-bold text-neon">{s.value}</span>
          <span className="text-xs uppercase tracking-wide text-white/50">{s.label}</span>
        </div>
      ))}
    </div>
  );
}
