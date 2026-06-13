'use client';
import { useState } from 'react';
import { createClient } from '@/lib/supabase/client';

export default function LoginButton() {
  const [loading, setLoading] = useState(false);

  async function signIn() {
    setLoading(true);
    const supabase = createClient();
    await supabase.auth.signInWithOAuth({
      provider: 'google',
      options: {
        redirectTo: `${window.location.origin}/auth/callback`,
      },
    });
  }

  return (
    <button onClick={signIn} disabled={loading} className="btn-neon text-base">
      <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden>
        <path
          fill="currentColor"
          d="M21.35 11.1H12v2.9h5.35c-.23 1.5-1.6 4.4-5.35 4.4a5.9 5.9 0 0 1 0-11.8c1.68 0 2.8.72 3.44 1.34l2.35-2.27C16.4 4.2 14.4 3.3 12 3.3a8.7 8.7 0 1 0 0 17.4c5.02 0 8.34-3.53 8.34-8.5 0-.57-.06-1-.14-1.4Z"
        />
      </svg>
      {loading ? 'Connecting…' : 'Continue with Google'}
    </button>
  );
}
