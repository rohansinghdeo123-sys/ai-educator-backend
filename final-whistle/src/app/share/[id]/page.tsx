import { notFound } from 'next/navigation';
import Link from 'next/link';
import type { Metadata } from 'next';
import { createAdminClient } from '@/lib/supabase/server';

export const dynamic = 'force-dynamic';

// Public read of a single prediction (opt-in share). Uses the service-role
// client because predictions are otherwise owner-only under RLS.
async function loadCard(id: string) {
  const db = createAdminClient();
  const { data: pred } = await db
    .from('predictions')
    .select('*, match:match_id(*, home_team:home_team_id(*), away_team:away_team_id(*))')
    .eq('id', id)
    .maybeSingle();
  if (!pred) return null;
  const { data: profile } = await db
    .from('profiles')
    .select('username, fan_title')
    .eq('id', pred.user_id)
    .single();
  return { pred, profile };
}

export async function generateMetadata({ params }: { params: { id: string } }): Promise<Metadata> {
  const card = await loadCard(params.id);
  if (!card) return { title: 'Final Whistle' };
  const { pred, profile } = card;
  const m = pred.match;
  const title = `${profile?.username ?? 'A fan'}'s prediction: ${m.home_team?.short_name} ${pred.home_score}–${pred.away_score} ${m.away_team?.short_name}`;
  return {
    title,
    description: 'Predict daily football matches on Final Whistle ⚽',
    openGraph: { title, description: 'Join me on Final Whistle ⚽' },
  };
}

export default async function SharePage({ params }: { params: { id: string } }) {
  const card = await loadCard(params.id);
  if (!card) notFound();
  const { pred, profile } = card;
  const m = pred.match;
  const settled = m.status === 'settled' || m.status === 'finished';

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col items-center justify-center px-6 py-10">
      <div className="glass w-full overflow-hidden p-6 text-center">
        <div className="text-xs uppercase tracking-widest text-neon">Final Whistle</div>
        <div className="mt-1 text-sm text-white/60">
          {profile?.username ?? 'A fan'} · {profile?.fan_title}
        </div>

        <div className="my-6 flex items-center justify-around">
          <div className="flex flex-col items-center gap-1">
            <div
              className="flex h-14 w-14 items-center justify-center rounded-xl font-display font-bold"
              style={{ background: `${m.home_team?.color}22`, color: m.home_team?.color }}
            >
              {m.home_team?.short_name}
            </div>
          </div>
          <div className="font-display text-4xl font-extrabold text-neon">
            {pred.home_score}–{pred.away_score}
          </div>
          <div className="flex flex-col items-center gap-1">
            <div
              className="flex h-14 w-14 items-center justify-center rounded-xl font-display font-bold"
              style={{ background: `${m.away_team?.color}22`, color: m.away_team?.color }}
            >
              {m.away_team?.short_name}
            </div>
          </div>
        </div>

        <div className="text-xs uppercase tracking-widest text-white/40">{m.stage} · my prediction</div>

        {settled && (
          <div className="mt-4 rounded-xl bg-white/5 px-4 py-2 text-sm">
            Final: {m.home_score}–{m.away_score} ·{' '}
            <span className="font-semibold text-neon">+{pred.xp_awarded} XP</span>
          </div>
        )}
      </div>

      <Link href="/" className="btn-neon mt-6">
        Play Final Whistle
      </Link>
      <p className="mt-4 text-center text-xs text-white/40">
        Independent fan game. Team names are fictional.
      </p>
    </main>
  );
}
