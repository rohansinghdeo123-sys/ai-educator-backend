'use client';
import { createBrowserClient } from '@supabase/ssr';

// Browser-side Supabase client (uses the public anon key + user session cookie).
export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
  );
}
