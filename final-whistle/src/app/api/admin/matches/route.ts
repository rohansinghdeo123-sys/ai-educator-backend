import { NextResponse } from 'next/server';
import { requireAdmin } from '@/lib/admin';
import { createAdminClient } from '@/lib/supabase/server';

export async function POST(request: Request) {
  if (!(await requireAdmin())) return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
  const {
    home_team_id,
    away_team_id,
    kickoff_at,
    stage = 'Group Stage',
    bonus_event_defs = [],
  } = await request.json();

  if (!home_team_id || !away_team_id || !kickoff_at) {
    return NextResponse.json({ error: 'teams and kickoff_at required' }, { status: 400 });
  }
  if (home_team_id === away_team_id) {
    return NextResponse.json({ error: 'A team cannot play itself' }, { status: 400 });
  }

  const db = createAdminClient();
  const { data, error } = await db
    .from('matches')
    .insert({ home_team_id, away_team_id, kickoff_at, stage, bonus_event_defs })
    .select()
    .single();
  if (error) return NextResponse.json({ error: error.message }, { status: 400 });
  return NextResponse.json({ ok: true, match: data });
}

// Update match status (e.g. scheduled -> live) without settling.
export async function PATCH(request: Request) {
  if (!(await requireAdmin())) return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
  const { id, status } = await request.json();
  if (!id || !['scheduled', 'live', 'finished'].includes(status)) {
    return NextResponse.json({ error: 'Invalid update' }, { status: 400 });
  }
  const db = createAdminClient();
  const { error } = await db.from('matches').update({ status }).eq('id', id);
  if (error) return NextResponse.json({ error: error.message }, { status: 400 });
  return NextResponse.json({ ok: true });
}
