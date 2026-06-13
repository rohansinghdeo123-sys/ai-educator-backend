import { createServerSupabase } from '@/lib/supabase/server';
import CosmeticsStore from '@/components/CosmeticsStore';

export const dynamic = 'force-dynamic';

export default async function RewardsPage() {
  const supabase = createServerSupabase();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const [{ data: profile }, { data: allBadges }, { data: mine }, { data: ledger }] = await Promise.all([
    supabase.from('profiles').select('coins, fan_title, avatar_frame').eq('id', user!.id).single(),
    supabase.from('badges').select('*'),
    supabase.from('user_badges').select('badge_id').eq('user_id', user!.id),
    supabase.from('coin_transactions').select('reason').eq('user_id', user!.id).like('reason', 'unlock:%'),
  ]);

  const earned = new Set((mine ?? []).map((r) => r.badge_id));
  const unlocked = new Set((ledger ?? []).map((r) => (r.reason as string).replace('unlock:', '')));

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <h1 className="font-display text-2xl font-bold">🎖️ Rewards</h1>
        <span className="stat-pill text-gold">🪙 {profile?.coins ?? 0}</span>
      </div>

      <section>
        <h2 className="mb-3 font-display text-lg font-bold">Badges</h2>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          {(allBadges ?? []).map((b) => {
            const has = earned.has(b.id);
            return (
              <div
                key={b.id}
                className={`glass flex flex-col items-center p-4 text-center transition ${
                  has ? 'border-neon/40' : 'opacity-50 grayscale'
                }`}
              >
                <div className="text-3xl">{b.icon}</div>
                <div className="mt-1 text-sm font-semibold">{b.name}</div>
                <div className="text-xs text-white/50">{b.description}</div>
              </div>
            );
          })}
        </div>
      </section>

      <section>
        <h2 className="mb-3 font-display text-lg font-bold">Cosmetic Store</h2>
        <CosmeticsStore
          coins={profile?.coins ?? 0}
          fanTitle={profile?.fan_title ?? 'Rookie Fan'}
          frame={profile?.avatar_frame ?? 'none'}
          unlocked={unlocked}
        />
      </section>
    </div>
  );
}
