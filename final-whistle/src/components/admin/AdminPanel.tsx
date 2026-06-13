'use client';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import type { Match, Team } from '@/lib/types';

async function post(url: string, body: unknown, method = 'POST') {
  const res = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error ?? 'Request failed');
  return data;
}

export default function AdminPanel({ teams, matches }: { teams: Team[]; matches: Match[] }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  function run(fn: () => Promise<void>) {
    return async () => {
      setBusy(true);
      setMsg(null);
      try {
        await fn();
        router.refresh();
        setMsg('✓ Done');
      } catch (e) {
        setMsg((e as Error).message);
      } finally {
        setBusy(false);
      }
    };
  }

  return (
    <div className="space-y-8">
      {msg && <div className="glass px-4 py-2 text-sm text-neon">{msg}</div>}

      <CreateTeam onSubmit={(b) => post('/api/admin/teams', b)} run={run} busy={busy} />
      <CreateMatch teams={teams} onSubmit={(b) => post('/api/admin/matches', b)} run={run} busy={busy} />
      <SettleList matches={matches} run={run} busy={busy} />
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="glass p-5">
      <h2 className="mb-4 font-display text-lg font-bold">{title}</h2>
      {children}
    </section>
  );
}

const input =
  'w-full rounded-xl border border-white/10 bg-pitch-850 px-3 py-2 text-sm outline-none focus:border-neon';

function CreateTeam({
  onSubmit,
  run,
  busy,
}: {
  onSubmit: (b: unknown) => Promise<unknown>;
  run: (fn: () => Promise<void>) => () => void;
  busy: boolean;
}) {
  const [name, setName] = useState('');
  const [shortName, setShortName] = useState('');
  const [color, setColor] = useState('#39FF8B');
  return (
    <Section title="Add Team">
      <div className="grid gap-3 sm:grid-cols-3">
        <input className={input} placeholder="Team name" value={name} onChange={(e) => setName(e.target.value)} />
        <input className={input} placeholder="Short (3 letters)" value={shortName} onChange={(e) => setShortName(e.target.value)} />
        <input className={input} type="color" value={color} onChange={(e) => setColor(e.target.value)} />
      </div>
      <button
        disabled={busy}
        onClick={run(async () => {
          await onSubmit({ name, short_name: shortName, color });
          setName('');
          setShortName('');
        })}
        className="btn-neon mt-3 text-sm"
      >
        Create team
      </button>
    </Section>
  );
}

function CreateMatch({
  teams,
  onSubmit,
  run,
  busy,
}: {
  teams: Team[];
  onSubmit: (b: unknown) => Promise<unknown>;
  run: (fn: () => Promise<void>) => () => void;
  busy: boolean;
}) {
  const [home, setHome] = useState('');
  const [away, setAway] = useState('');
  const [kickoff, setKickoff] = useState('');
  const [stage, setStage] = useState('Group Stage');
  const [bonus, setBonus] = useState('Both teams to score, A red card shown');

  return (
    <Section title="Schedule Match">
      <div className="grid gap-3 sm:grid-cols-2">
        <select className={input} value={home} onChange={(e) => setHome(e.target.value)}>
          <option value="">Home team…</option>
          {teams.map((t) => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </select>
        <select className={input} value={away} onChange={(e) => setAway(e.target.value)}>
          <option value="">Away team…</option>
          {teams.map((t) => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </select>
        <input className={input} type="datetime-local" value={kickoff} onChange={(e) => setKickoff(e.target.value)} />
        <input className={input} placeholder="Stage" value={stage} onChange={(e) => setStage(e.target.value)} />
      </div>
      <input
        className={`${input} mt-3`}
        placeholder="Bonus events (comma separated)"
        value={bonus}
        onChange={(e) => setBonus(e.target.value)}
      />
      <button
        disabled={busy}
        onClick={run(async () => {
          const defs = bonus
            .split(',')
            .map((s) => s.trim())
            .filter(Boolean)
            .map((label, i) => ({ key: `b${i}`, label }));
          await onSubmit({
            home_team_id: home,
            away_team_id: away,
            kickoff_at: new Date(kickoff).toISOString(),
            stage,
            bonus_event_defs: defs,
          });
          setHome('');
          setAway('');
          setKickoff('');
        })}
        className="btn-neon mt-3 text-sm"
      >
        Schedule match
      </button>
    </Section>
  );
}

function SettleList({
  matches,
  run,
  busy,
}: {
  matches: Match[];
  run: (fn: () => Promise<void>) => () => void;
  busy: boolean;
}) {
  return (
    <Section title="Results & Settlement">
      <div className="space-y-3">
        {matches.length === 0 && <p className="text-sm text-white/50">No matches yet.</p>}
        {matches.map((m) => (
          <SettleRow key={m.id} match={m} run={run} busy={busy} />
        ))}
      </div>
    </Section>
  );
}

function SettleRow({
  match,
  run,
  busy,
}: {
  match: Match;
  run: (fn: () => Promise<void>) => () => void;
  busy: boolean;
}) {
  const [hs, setHs] = useState(match.home_score ?? 0);
  const [as, setAs] = useState(match.away_score ?? 0);
  const [bonus, setBonus] = useState<Record<string, boolean>>(match.bonus_event_results ?? {});
  const settled = match.status === 'settled';

  return (
    <div className="rounded-xl border border-white/10 bg-white/5 p-3">
      <div className="flex items-center justify-between text-sm">
        <span className="font-semibold">
          {match.home_team?.short_name} vs {match.away_team?.short_name}
        </span>
        <span className={settled ? 'text-neon' : 'text-gold'}>{match.status}</span>
      </div>
      <div className="mt-2 flex items-center gap-2">
        <input
          type="number"
          min={0}
          value={hs}
          onChange={(e) => setHs(Number(e.target.value))}
          className={`${input} w-16 text-center`}
        />
        <span>–</span>
        <input
          type="number"
          min={0}
          value={as}
          onChange={(e) => setAs(Number(e.target.value))}
          className={`${input} w-16 text-center`}
        />
      </div>
      {match.bonus_event_defs.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {match.bonus_event_defs.map((b) => (
            <label key={b.key} className="flex items-center gap-1 text-xs text-white/70">
              <input
                type="checkbox"
                checked={!!bonus[b.key]}
                onChange={(e) => setBonus((p) => ({ ...p, [b.key]: e.target.checked }))}
                className="accent-[#39FF8B]"
              />
              {b.label}
            </label>
          ))}
        </div>
      )}
      <button
        disabled={busy}
        onClick={run(async () => {
          await post('/api/admin/settle', {
            match_id: match.id,
            home_score: hs,
            away_score: as,
            bonus_event_results: bonus,
          });
        })}
        className="btn-neon mt-3 text-xs"
      >
        {settled ? 'Re-settle' : 'Settle & award XP'}
      </button>
    </div>
  );
}
