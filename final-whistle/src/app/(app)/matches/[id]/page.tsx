import { notFound } from 'next/navigation';
import Link from 'next/link';
import { createServerSupabase } from '@/lib/supabase/server';
import PredictionForm from '@/components/PredictionForm';
import ShareButton from '@/components/ShareButton';
import { kickoffTime } from '@/lib/format';
import type { Match, Prediction } from '@/lib/types';

export const dynamic = 'force-dynamic';

export default async function MatchPage({ params }: { params: { id: string } }) {
  const supabase = createServerSupabase();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  const { data: match } = await supabase
    .from('matches')
    .select('*, home_team:home_team_id(*), away_team:away_team_id(*)')
    .eq('id', params.id)
    .single();
  if (!match) notFound();
  const m = match as Match;

  const { data: existing } = await supabase
    .from('predictions')
    .select('*')
    .eq('match_id', params.id)
    .eq('user_id', user!.id)
    .maybeSingle();
  const prediction = existing as Prediction | null;

  const settled = m.status === 'settled' || m.status === 'finished';

  return (
    <div className="space-y-6">
      <Link href="/dashboard" className="text-sm text-white/50">
        ← Back
      </Link>

      <div className="text-center">
        <div className="text-xs uppercase tracking-widest text-white/40">{m.stage}</div>
        <h1 className="mt-1 font-display text-2xl font-bold">
          {m.home_team?.name} <span className="text-white/40">vs</span> {m.away_team?.name}
        </h1>
        <div className="mt-1 text-sm text-white/50">{kickoffTime(m.kickoff_at)}</div>
      </div>

      {settled ? (
        <div className="glass p-6 text-center">
          <div className="text-xs uppercase tracking-widest text-white/40">Full time</div>
          <div className="my-2 font-display text-5xl font-extrabold">
            {m.home_score}–{m.away_score}
          </div>
          {prediction ? (
            <div className="mt-4">
              <div className="text-sm text-white/60">
                You predicted {prediction.home_score}–{prediction.away_score}
              </div>
              <div className="mt-1 font-display text-xl font-bold text-neon">
                +{prediction.xp_awarded} XP earned
              </div>
            </div>
          ) : (
            <p className="mt-3 text-sm text-white/50">You didn&apos;t predict this match.</p>
          )}
        </div>
      ) : (
        <PredictionForm match={m} existing={prediction} />
      )}

      {prediction && <ShareButton predictionId={prediction.id} />}
    </div>
  );
}
