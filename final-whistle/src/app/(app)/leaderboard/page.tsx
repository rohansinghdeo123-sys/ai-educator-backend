import { createServerSupabase } from '@/lib/supabase/server';
import LeaderboardPodium from '@/components/LeaderboardPodium';
import type { LeaderboardRow } from '@/lib/types';

export const dynamic = 'force-dynamic';

export default async function LeaderboardPage() {
  const supabase = createServerSupabase();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const { data } = await supabase
    .from('profiles')
    .select('id, username, avatar_url, xp, streak, fan_title, avatar_frame')
    .order('xp', { ascending: false })
    .limit(50);

  const rows: LeaderboardRow[] = (data ?? []).map((r, i) => ({ ...r, rank: i + 1 } as LeaderboardRow));
  const top = rows.slice(0, 3);
  const rest = rows.slice(3);
  const myRow = rows.find((r) => r.id === user?.id);

  return (
    <div className="space-y-6">
      <h1 className="font-display text-2xl font-bold">🏆 Leaderboard</h1>

      {rows.length === 0 ? (
        <div className="glass p-8 text-center text-white/50">No players yet — be the first!</div>
      ) : (
        <>
          <div className="glass p-5">
            <LeaderboardPodium top={top} />
          </div>

          <div className="space-y-2">
            {rest.map((r) => (
              <Row key={r.id} r={r} me={r.id === user?.id} />
            ))}
          </div>

          {myRow && myRow.rank > 3 && (
            <div className="sticky bottom-20">
              <Row r={myRow} me highlight />
            </div>
          )}
        </>
      )}
    </div>
  );
}

function Row({ r, me, highlight }: { r: LeaderboardRow; me?: boolean; highlight?: boolean }) {
  return (
    <div
      className={`flex items-center gap-3 rounded-xl border px-4 py-3 ${
        highlight ? 'border-neon bg-neon/10' : 'border-white/10 bg-white/5'
      }`}
    >
      <span className="w-6 text-center font-display font-bold text-white/60">{r.rank}</span>
      <div className="flex h-9 w-9 items-center justify-center rounded-full bg-pitch-700 font-bold">
        {(r.username ?? '?')[0].toUpperCase()}
      </div>
      <div className="flex-1">
        <div className="text-sm font-semibold">
          {r.username ?? 'Player'} {me && <span className="text-xs text-neon">(you)</span>}
        </div>
        <div className="text-xs text-white/40">{r.fan_title}</div>
      </div>
      <div className="text-right">
        <div className="font-display font-bold text-neon">{r.xp.toLocaleString()}</div>
        <div className="text-xs text-white/40">🔥 {r.streak}</div>
      </div>
    </div>
  );
}
