import { NextResponse } from 'next/server';
import { createServerSupabase } from '@/lib/supabase/server';
import type { ConfidenceLevel, PredictedResult } from '@/lib/types';

// Create or update a prediction. Rejected once the match has kicked off.
export async function POST(request: Request) {
  const supabase = createServerSupabase();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const body = await request.json();
  const {
    match_id,
    predicted_winner,
    home_score,
    away_score,
    confidence = 'medium',
    bonus_events = {},
  } = body as {
    match_id: string;
    predicted_winner: PredictedResult;
    home_score: number;
    away_score: number;
    confidence: ConfidenceLevel;
    bonus_events: Record<string, boolean>;
  };

  // Validate inputs.
  if (!match_id || !['home', 'away', 'draw'].includes(predicted_winner)) {
    return NextResponse.json({ error: 'Invalid prediction' }, { status: 400 });
  }
  const hs = Number(home_score);
  const as = Number(away_score);
  if (!Number.isInteger(hs) || !Number.isInteger(as) || hs < 0 || as < 0 || hs > 20 || as > 20) {
    return NextResponse.json({ error: 'Invalid scoreline' }, { status: 400 });
  }
  // Scoreline must be consistent with the chosen winner.
  const impliedWinner = hs > as ? 'home' : as > hs ? 'away' : 'draw';
  if (impliedWinner !== predicted_winner) {
    return NextResponse.json(
      { error: 'Scoreline does not match your selected result' },
      { status: 400 }
    );
  }

  // Lock check.
  const { data: match } = await supabase
    .from('matches')
    .select('kickoff_at, status')
    .eq('id', match_id)
    .single();
  if (!match) return NextResponse.json({ error: 'Match not found' }, { status: 404 });
  if (new Date(match.kickoff_at).getTime() <= Date.now() || match.status !== 'scheduled') {
    return NextResponse.json({ error: 'Predictions are closed for this match' }, { status: 403 });
  }

  // Upsert (unique on user_id + match_id). Only mutable while unsettled (RLS).
  const { error } = await supabase.from('predictions').upsert(
    {
      user_id: user.id,
      match_id,
      predicted_winner,
      home_score: hs,
      away_score: as,
      confidence,
      bonus_events,
    },
    { onConflict: 'user_id,match_id' }
  );

  if (error) return NextResponse.json({ error: error.message }, { status: 400 });
  return NextResponse.json({ ok: true });
}
