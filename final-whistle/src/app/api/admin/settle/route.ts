import { NextResponse } from 'next/server';
import { requireAdmin } from '@/lib/admin';
import { createAdminClient } from '@/lib/supabase/server';
import { computeScore, streakMilestoneReward } from '@/lib/scoring';
import type { Prediction, Profile } from '@/lib/types';

// Settle a match: record the result, score every prediction, update XP,
// streaks, coins and badges. Idempotent — only settles unsettled predictions.
export async function POST(request: Request) {
  const adminId = await requireAdmin();
  if (!adminId) return NextResponse.json({ error: 'Forbidden' }, { status: 403 });

  const { match_id, home_score, away_score, bonus_event_results = {} } = await request.json();
  if (!match_id || home_score == null || away_score == null) {
    return NextResponse.json({ error: 'match_id and scores required' }, { status: 400 });
  }

  const db = createAdminClient();

  // 1. Record the result on the match.
  const { error: mErr } = await db
    .from('matches')
    .update({
      home_score,
      away_score,
      bonus_event_results,
      status: 'settled',
    })
    .eq('id', match_id);
  if (mErr) return NextResponse.json({ error: mErr.message }, { status: 400 });

  // 2. Pull all unsettled predictions for this match.
  const { data: preds } = await db
    .from('predictions')
    .select('*')
    .eq('match_id', match_id)
    .eq('is_settled', false);
  const predictions = (preds ?? []) as Prediction[];

  const actual = {
    home_score: Number(home_score),
    away_score: Number(away_score),
    bonus_event_results: bonus_event_results as Record<string, boolean>,
  };

  // Load all involved profiles up front.
  const userIds = [...new Set(predictions.map((p) => p.user_id))];
  const { data: profs } = await db.from('profiles').select('*').in('id', userIds);
  const profileMap = new Map<string, Profile>((profs ?? []).map((p) => [p.id, p as Profile]));

  let settled = 0;
  const badgeAwards: { user_id: string; code: string }[] = [];

  for (const pred of predictions) {
    const result = computeScore(pred, actual);
    const profile = profileMap.get(pred.user_id);
    if (!profile) continue;

    // Update the prediction.
    await db
      .from('predictions')
      .update({ xp_awarded: result.xp, is_settled: true })
      .eq('id', pred.id);

    // Update streak.
    const newStreak = result.resultCorrect ? profile.streak + 1 : 0;
    const newBest = Math.max(profile.best_streak, newStreak);
    const newXp = Math.max(0, profile.xp + result.xp);

    await db
      .from('profiles')
      .update({ xp: newXp, streak: newStreak, best_streak: newBest })
      .eq('id', pred.user_id);
    profile.xp = newXp;
    profile.streak = newStreak;
    profile.best_streak = newBest;

    // Streak milestone coins.
    const coinReward = streakMilestoneReward(newStreak);
    if (coinReward > 0) {
      await db.rpc('award_coins', {
        p_user: pred.user_id,
        p_amount: coinReward,
        p_reason: `Streak ${newStreak} bonus`,
      });
    }

    // Badge triggers.
    if (result.exactScore) badgeAwards.push({ user_id: pred.user_id, code: 'exact_score' });
    if (newStreak >= 3) badgeAwards.push({ user_id: pred.user_id, code: 'streak_3' });
    if (newStreak >= 10) badgeAwards.push({ user_id: pred.user_id, code: 'streak_10' });

    settled += 1;
  }

  // Award badges (resolve codes -> ids, ignore duplicates).
  if (badgeAwards.length > 0) {
    const codes = [...new Set(badgeAwards.map((b) => b.code))];
    const { data: badges } = await db.from('badges').select('id, code').in('code', codes);
    const idByCode = new Map((badges ?? []).map((b) => [b.code, b.id]));
    const rows = badgeAwards
      .map((b) => ({ user_id: b.user_id, badge_id: idByCode.get(b.code) }))
      .filter((r) => r.badge_id);
    if (rows.length > 0) {
      await db.from('user_badges').upsert(rows, { onConflict: 'user_id,badge_id', ignoreDuplicates: true });
    }
  }

  return NextResponse.json({ ok: true, settled });
}
