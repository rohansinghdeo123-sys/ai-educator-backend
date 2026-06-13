'use client';
import { useRouter } from 'next/navigation';
import { createClient } from '@/lib/supabase/client';

export default function SignOutButton() {
  const router = useRouter();
  async function signOut() {
    await createClient().auth.signOut();
    router.push('/');
    router.refresh();
  }
  return (
    <button onClick={signOut} className="btn-ghost w-full text-sm">
      Sign out
    </button>
  );
}
