import { NextResponse } from 'next/server';
import { requireAdmin } from '@/lib/admin';
import { createAdminClient } from '@/lib/supabase/server';

export async function POST(request: Request) {
  if (!(await requireAdmin())) return NextResponse.json({ error: 'Forbidden' }, { status: 403 });
  const { name, short_name, color = '#39FF8B', crest_url = null } = await request.json();
  if (!name || !short_name) {
    return NextResponse.json({ error: 'name and short_name required' }, { status: 400 });
  }
  const db = createAdminClient();
  const { data, error } = await db
    .from('teams')
    .insert({ name, short_name, color, crest_url })
    .select()
    .single();
  if (error) return NextResponse.json({ error: error.message }, { status: 400 });
  return NextResponse.json({ ok: true, team: data });
}
