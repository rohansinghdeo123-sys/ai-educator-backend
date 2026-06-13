import { NextResponse } from 'next/server';
import { createServerSupabase, createAdminClient } from '@/lib/supabase/server';
import { findCosmetic } from '@/lib/cosmetics';

// Equip a cosmetic. First-time unlock spends coins (recorded in the ledger);
// re-equipping something already unlocked is free.
export async function POST(request: Request) {
  const supabase = createServerSupabase();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { kind, id } = await request.json();
  if (kind !== 'title' && kind !== 'frame') {
    return NextResponse.json({ error: 'Invalid kind' }, { status: 400 });
  }
  const cosmetic = findCosmetic(kind, id);
  if (!cosmetic) return NextResponse.json({ error: 'Unknown cosmetic' }, { status: 404 });

  const db = createAdminClient();
  const reason = `unlock:${kind}:${id}`;

  // Already unlocked?
  const { data: prior } = await db
    .from('coin_transactions')
    .select('id')
    .eq('user_id', user.id)
    .eq('reason', reason)
    .maybeSingle();

  if (!prior && cosmetic.cost > 0) {
    const { data: profile } = await db.from('profiles').select('coins').eq('id', user.id).single();
    if (!profile || profile.coins < cosmetic.cost) {
      return NextResponse.json({ error: 'Not enough coins' }, { status: 400 });
    }
    await db.rpc('award_coins', { p_user: user.id, p_amount: -cosmetic.cost, p_reason: reason });
  }

  const field = kind === 'title' ? 'fan_title' : 'avatar_frame';
  await db.from('profiles').update({ [field]: id }).eq('id', user.id);

  return NextResponse.json({ ok: true });
}
