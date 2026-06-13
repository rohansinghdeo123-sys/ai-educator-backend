import { redirect } from 'next/navigation';
import Link from 'next/link';
import { createServerSupabase } from '@/lib/supabase/server';
import { requireAdmin } from '@/lib/admin';
import AdminPanel from '@/components/admin/AdminPanel';
import type { Match, Team } from '@/lib/types';

export const dynamic = 'force-dynamic';

export default async function AdminPage() {
  if (!(await requireAdmin())) redirect('/dashboard');

  const supabase = createServerSupabase();
  const [{ data: teams }, { data: matches }] = await Promise.all([
    supabase.from('teams').select('*').order('name'),
    supabase
      .from('matches')
      .select('*, home_team:home_team_id(*), away_team:away_team_id(*)')
      .order('kickoff_at', { ascending: false })
      .limit(40),
  ]);

  return (
    <div className="mx-auto min-h-dvh max-w-3xl px-4 py-6">
      <header className="mb-6 flex items-center justify-between">
        <h1 className="font-display text-2xl font-bold">⚙️ Admin Console</h1>
        <Link href="/dashboard" className="btn-ghost text-sm">
          Exit
        </Link>
      </header>
      <AdminPanel teams={(teams as Team[]) ?? []} matches={(matches as Match[]) ?? []} />
    </div>
  );
}
