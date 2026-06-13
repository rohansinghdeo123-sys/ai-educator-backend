'use client';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import type { ConfidenceLevel, Match, PredictedResult, Prediction } from '@/lib/types';

const CONF: { key: ConfidenceLevel; label: string; mult: string }[] = [
  { key: 'low', label: 'Low', mult: '×1.0' },
  { key: 'medium', label: 'Medium', mult: '×1.25' },
  { key: 'high', label: 'High', mult: '×1.5' },
];

export default function PredictionForm({
  match,
  existing,
}: {
  match: Match;
  existing: Prediction | null;
}) {
  const router = useRouter();
  const [home, setHome] = useState(existing?.home_score ?? 1);
  const [away, setAway] = useState(existing?.away_score ?? 0);
  const [confidence, setConfidence] = useState<ConfidenceLevel>(existing?.confidence ?? 'medium');
  const [bonus, setBonus] = useState<Record<string, boolean>>(existing?.bonus_events ?? {});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const winner: PredictedResult = home > away ? 'home' : away > home ? 'away' : 'draw';
  const locked = new Date(match.kickoff_at).getTime() <= Date.now() || match.status !== 'scheduled';

  function step(side: 'home' | 'away', delta: number) {
    if (side === 'home') setHome((v) => Math.max(0, Math.min(20, v + delta)));
    else setAway((v) => Math.max(0, Math.min(20, v + delta)));
  }

  async function submit() {
    setSaving(true);
    setError(null);
    const res = await fetch('/api/predictions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        match_id: match.id,
        predicted_winner: winner,
        home_score: home,
        away_score: away,
        confidence,
        bonus_events: bonus,
      }),
    });
    setSaving(false);
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      setError(d.error ?? 'Could not save prediction');
      return;
    }
    router.push('/dashboard');
    router.refresh();
  }

  const winnerLabel =
    winner === 'home'
      ? match.home_team?.short_name
      : winner === 'away'
      ? match.away_team?.short_name
      : 'Draw';

  return (
    <div className="space-y-6">
      {/* Scoreline steppers */}
      <div className="glass p-5">
        <div className="mb-4 text-center text-xs uppercase tracking-widest text-white/50">
          Predict the scoreline
        </div>
        <div className="flex items-center justify-around">
          {(['home', 'away'] as const).map((side) => {
            const team = side === 'home' ? match.home_team : match.away_team;
            const val = side === 'home' ? home : away;
            return (
              <div key={side} className="flex flex-col items-center gap-2">
                <span className="text-sm font-semibold">{team?.short_name}</span>
                <button
                  onClick={() => step(side, 1)}
                  disabled={locked}
                  className="btn-ghost h-8 w-10 !px-0 !py-0 text-lg"
                >
                  ▲
                </button>
                <span className="font-display text-4xl font-extrabold text-neon">{val}</span>
                <button
                  onClick={() => step(side, -1)}
                  disabled={locked}
                  className="btn-ghost h-8 w-10 !px-0 !py-0 text-lg"
                >
                  ▼
                </button>
              </div>
            );
          })}
        </div>
        <div className="mt-4 text-center text-sm text-white/70">
          Predicted result: <span className="font-semibold text-gold">{winnerLabel}</span>
        </div>
      </div>

      {/* Confidence */}
      <div className="glass p-5">
        <div className="mb-3 text-xs uppercase tracking-widest text-white/50">Confidence</div>
        <div className="grid grid-cols-3 gap-2">
          {CONF.map((c) => (
            <button
              key={c.key}
              onClick={() => setConfidence(c.key)}
              disabled={locked}
              className={`rounded-xl border px-3 py-3 text-center transition ${
                confidence === c.key
                  ? 'border-neon bg-neon/10 text-neon'
                  : 'border-white/10 bg-white/5 text-white/70'
              }`}
            >
              <div className="font-semibold">{c.label}</div>
              <div className="text-xs opacity-70">{c.mult} XP</div>
            </button>
          ))}
        </div>
        <p className="mt-2 text-xs text-white/40">
          High confidence multiplies your XP — but a wrong result costs you points.
        </p>
      </div>

      {/* Bonus events */}
      {match.bonus_event_defs.length > 0 && (
        <div className="glass p-5">
          <div className="mb-3 text-xs uppercase tracking-widest text-white/50">
            Bonus events (+5 XP each)
          </div>
          <div className="space-y-2">
            {match.bonus_event_defs.map((b) => (
              <label
                key={b.key}
                className="flex cursor-pointer items-center justify-between rounded-xl border border-white/10 bg-white/5 px-4 py-3"
              >
                <span className="text-sm">{b.label}</span>
                <input
                  type="checkbox"
                  checked={!!bonus[b.key]}
                  disabled={locked}
                  onChange={(e) => setBonus((p) => ({ ...p, [b.key]: e.target.checked }))}
                  className="h-5 w-5 accent-[#39FF8B]"
                />
              </label>
            ))}
          </div>
        </div>
      )}

      {error && <p className="text-center text-sm text-danger">{error}</p>}

      <button onClick={submit} disabled={saving || locked} className="btn-neon w-full text-base">
        {locked ? 'Predictions closed' : saving ? 'Saving…' : existing ? 'Update prediction' : 'Lock in prediction'}
      </button>
    </div>
  );
}
