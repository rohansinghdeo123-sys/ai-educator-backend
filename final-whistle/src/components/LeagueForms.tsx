'use client';
import { useRouter } from 'next/navigation';
import { useState } from 'react';

export default function LeagueForms() {
  const router = useRouter();
  const [name, setName] = useState('');
  const [code, setCode] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function call(url: string, body: unknown, ok: string) {
    setBusy(true);
    setMsg(null);
    try {
      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await res.json();
      if (!res.ok) throw new Error(d.error ?? 'Failed');
      setMsg(ok);
      setName('');
      setCode('');
      router.refresh();
    } catch (e) {
      setMsg((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const input = 'flex-1 rounded-xl border border-white/10 bg-pitch-850 px-3 py-2 text-sm outline-none focus:border-neon';

  return (
    <div className="space-y-3">
      <div className="glass p-4">
        <div className="mb-2 text-xs uppercase tracking-widest text-white/50">Create a league</div>
        <div className="flex gap-2">
          <input className={input} placeholder="League name" value={name} onChange={(e) => setName(e.target.value)} />
          <button disabled={busy} onClick={() => call('/api/leagues', { name }, '✓ League created')} className="btn-neon text-sm">
            Create
          </button>
        </div>
      </div>
      <div className="glass p-4">
        <div className="mb-2 text-xs uppercase tracking-widest text-white/50">Join with a code</div>
        <div className="flex gap-2">
          <input
            className={`${input} uppercase`}
            placeholder="ABC123"
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            maxLength={6}
          />
          <button disabled={busy} onClick={() => call('/api/leagues/join', { code }, '✓ Joined league')} className="btn-ghost text-sm">
            Join
          </button>
        </div>
      </div>
      {msg && <p className="text-center text-sm text-neon">{msg}</p>}
    </div>
  );
}
