import { createServerSupabase } from './supabase/server';

// Returns the user id if the caller is an authenticated admin, else null.
export async function requireAdmin(): Promise<string | null> {
  const supabase = createServerSupabase();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) return null;
  const { data } = await supabase.from('profiles').select('is_admin').eq('id', user.id).single();
  return data?.is_admin ? user.id : null;
}
