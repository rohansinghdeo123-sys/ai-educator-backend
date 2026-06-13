import { NextResponse } from 'next/server';
import { createServerSupabase, createAdminClient } from '@/lib/supabase/server';

// Record a shootout result and award coins (2 per goal, capped to deter spam).
export async function POST(request: Request) {
  const supabase = createServerSupabase();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { score } = await request.json();
  const goals = Number(score);
  if (!Number.isInteger(goals) || goals < 0 || goals > 60) {
    return NextResponse.json({ error: 'Invalid score' }, { status: 400 });
  }

  const coins = goals * 2;
  const db = createAdminClient();

  await db.from('shootout_scores').insert({ user_id: user.id, score: goals });
  if (coins > 0) {
    await db.rpc('award_coins', { p_user: user.id, p_amount: coins, p_reason: 'Penalty shootout' });
  }

  // Spot-Kick Hero badge at 5+ goals.
  if (goals >= 5) {
    const { data: badge } = await db.from('badges').select('id').eq('code', 'shootout_5').single();
    if (badge) {
      await db
        .from('user_badges')
        .upsert({ user_id: user.id, badge_id: badge.id }, { onConflict: 'user_id,badge_id', ignoreDuplicates: true });
    }
  }

  return NextResponse.json({ ok: true, coins_awarded: coins });
}
