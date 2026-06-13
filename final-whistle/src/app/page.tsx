import { redirect } from 'next/navigation';
import { createServerSupabase } from '@/lib/supabase/server';
import LoginButton from '@/components/LoginButton';

export default async function LandingPage() {
  const supabase = createServerSupabase();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (user) redirect('/dashboard');

  return (
    <main className="relative mx-auto flex min-h-dvh max-w-5xl flex-col items-center justify-center px-6 py-16 text-center">
      {/* Pitch glow */}
      <div className="pointer-events-none absolute inset-x-0 top-0 h-64 bg-pitch-lines" />

      <span className="stat-pill mb-6 animate-pulse-glow text-neon">
        ● Live this tournament season
      </span>

      <h1 className="font-display text-5xl font-extrabold leading-tight sm:text-7xl">
        Final <span className="text-neon drop-shadow-[0_0_18px_rgba(57,255,139,0.5)]">Whistle</span>
      </h1>

      <p className="mt-5 max-w-xl text-lg text-white/70">
        Predict today&apos;s matches, take a 30-second penalty shootout, and climb the
        leaderboard. Earn XP, build your streak, and win the night.
      </p>

      <div className="mt-9">
        <LoginButton />
      </div>

      {/* Feature glass cards */}
      <div className="mt-16 grid w-full gap-4 sm:grid-cols-3">
        {[
          { icon: '🎯', title: 'Daily Predictions', body: 'Winner, exact score, confidence & bonus events.' },
          { icon: '🥅', title: 'Penalty Shootout', body: 'A fast 30-second mini-game for extra coins.' },
          { icon: '🏆', title: 'Leaderboards', body: 'Daily, overall & private leagues with friends.' },
        ].map((f) => (
          <div key={f.title} className="glass p-6 text-left">
            <div className="text-3xl">{f.icon}</div>
            <h3 className="mt-3 font-display text-lg font-bold">{f.title}</h3>
            <p className="mt-1 text-sm text-white/60">{f.body}</p>
          </div>
        ))}
      </div>

      <p className="mt-12 text-xs text-white/40">
        An independent fan game. Not affiliated with any official football organisation.
        Team names and crests are fictional.
      </p>
    </main>
  );
}
