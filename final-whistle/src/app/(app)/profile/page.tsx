import Link from 'next/link';
import { getProfile, getRank } from '@/lib/queries';
import { createServerSupabase } from '@/lib/supabase/server';
import { frameRing } from '@/lib/cosmetics';
import SignOutButton from '@/components/SignOutButton';
import { ordinal } from '@/lib/format';

export const dynamic = 'force-dynamic';

export default async function ProfilePage() {
  const profile = await getProfile();
  if (!profile) return null;

  const supabase = createServerSupabase();
  const [{ count: badgeCount }, { count: shootoutBest }, rank] = await Promise.all([
    supabase.from('user_badges').select('badge_id', { count: 'exact', head: true }).eq('user_id', profile.id),
    supabase.from('shootout_scores').select('score', { count: 'exact', head: true }).eq('user_id', profile.id),
    getRank(profile.xp),
  ]);

  return (
    <div className="space-y-6">
      <div className="glass flex flex-col items-center p-6 text-center">
        <div className={`flex h-20 w-20 items-center justify-center rounded-full bg-pitch-700 text-3xl ${frameRing(profile.avatar_frame)}`}>
          {(profile.username ?? '?')[0].toUpperCase()}
        </div>
        <h1 className="mt-3 font-display text-xl font-bold">{profile.username ?? 'Player'}</h1>
        <span className="mt-1 stat-pill text-gold">{profile.fan_title}</span>
      </div>

      <div className="grid grid-cols-2 gap-3">
        {[
          { label: 'Total XP', value: profile.xp.toLocaleString() },
          { label: 'Coins', value: profile.coins.toLocaleString() },
          { label: 'Best Streak', value: `🔥 ${profile.best_streak}` },
          { label: 'Overall Rank', value: ordinal(rank) },
          { label: 'Badges', value: String(badgeCount ?? 0) },
          { label: 'Games Played', value: String(shootoutBest ?? 0) },
        ].map((s) => (
          <div key={s.label} className="glass px-4 py-3">
            <div className="font-display text-xl font-bold text-neon">{s.value}</div>
            <div className="text-xs uppercase tracking-wide text-white/50">{s.label}</div>
          </div>
        ))}
      </div>

      <div className="space-y-2">
        <Link href="/rewards" className="btn-neon w-full">
          🎖️ Rewards & Store
        </Link>
        {profile.is_admin && (
          <Link href="/admin" className="btn-ghost w-full">
            ⚙️ Admin Console
          </Link>
        )}
        <SignOutButton />
      </div>
    </div>
  );
}
