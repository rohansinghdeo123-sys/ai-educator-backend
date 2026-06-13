import type { LeaderboardRow } from '@/lib/types';

function Avatar({ row, size }: { row: LeaderboardRow; size: number }) {
  const initial = (row.username ?? '?')[0].toUpperCase();
  return (
    <div
      className="flex items-center justify-center rounded-full bg-pitch-700 font-display font-bold text-white"
      style={{ width: size, height: size, fontSize: size / 2.6 }}
    >
      {initial}
    </div>
  );
}

// Top-3 podium: 2nd | 1st | 3rd
export default function LeaderboardPodium({ top }: { top: LeaderboardRow[] }) {
  const order = [top[1], top[0], top[2]].filter(Boolean);
  const meta: Record<number, { h: string; ring: string; medal: string }> = {
    1: { h: 'h-28', ring: 'ring-gold shadow-gold', medal: '🥇' },
    2: { h: 'h-20', ring: 'ring-white/40', medal: '🥈' },
    3: { h: 'h-16', ring: 'ring-amber-700/60', medal: '🥉' },
  };
  return (
    <div className="flex items-end justify-center gap-3">
      {order.map((row) => {
        const m = meta[row.rank] ?? meta[3];
        return (
          <div key={row.id} className="flex w-24 flex-col items-center">
            <div className={`mb-2 rounded-full ring-2 ${m.ring}`}>
              <Avatar row={row} size={row.rank === 1 ? 64 : 52} />
            </div>
            <div className="max-w-full truncate text-xs font-semibold">
              {row.username?.split(' ')[0] ?? 'Player'}
            </div>
            <div className="text-xs text-neon">{row.xp.toLocaleString()} XP</div>
            <div className={`mt-2 w-full rounded-t-xl bg-white/5 ${m.h} flex items-start justify-center pt-2 text-2xl`}>
              {m.medal}
            </div>
          </div>
        );
      })}
    </div>
  );
}
