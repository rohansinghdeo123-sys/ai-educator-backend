import { NextResponse } from 'next/server';
import { createServerSupabase, createAdminClient } from '@/lib/supabase/server';

// Join an existing league by its share code.
export async function POST(request: Request) {
  const supabase = createServerSupabase();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { code } = await request.json();
  if (!code) return NextResponse.json({ error: 'Code required' }, { status: 400 });

  const db = createAdminClient();
  const { data: league } = await db
    .from('leagues')
    .select('id, name')
    .eq('code', String(code).trim().toUpperCase())
    .single();
  if (!league) return NextResponse.json({ error: 'League not found' }, { status: 404 });

  await db
    .from('league_members')
    .upsert({ league_id: league.id, user_id: user.id }, { onConflict: 'league_id,user_id', ignoreDuplicates: true });

  return NextResponse.json({ ok: true, league });
}
