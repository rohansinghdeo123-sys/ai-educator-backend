import { NextResponse } from 'next/server';
import { createServerSupabase, createAdminClient } from '@/lib/supabase/server';

function makeCode(): string {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  return Array.from({ length: 6 }, () => chars[Math.floor(Math.random() * chars.length)]).join('');
}

// Create a private league and add the owner as the first member.
export async function POST(request: Request) {
  const supabase = createServerSupabase();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { name } = await request.json();
  if (!name || String(name).trim().length < 2) {
    return NextResponse.json({ error: 'League name too short' }, { status: 400 });
  }

  const db = createAdminClient();
  // Retry a few times in the unlikely event of a code collision.
  let league = null;
  for (let i = 0; i < 5 && !league; i++) {
    const code = makeCode();
    const { data, error } = await db
      .from('leagues')
      .insert({ name: String(name).trim(), code, owner_id: user.id })
      .select()
      .single();
    if (!error) league = data;
  }
  if (!league) return NextResponse.json({ error: 'Could not create league' }, { status: 500 });

  await db.from('league_members').insert({ league_id: league.id, user_id: user.id });
  return NextResponse.json({ ok: true, league });
}
