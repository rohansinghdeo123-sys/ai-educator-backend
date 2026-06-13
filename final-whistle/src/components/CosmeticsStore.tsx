'use client';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { AVATAR_FRAMES, FAN_TITLES, frameRing } from '@/lib/cosmetics';

export default function CosmeticsStore({
  coins,
  fanTitle,
  frame,
  unlocked,
}: {
  coins: number;
  fanTitle: string;
  frame: string;
  unlocked: Set<string>;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function equip(kind: 'title' | 'frame', id: string) {
    setBusy(`${kind}:${id}`);
    setErr(null);
    const res = await fetch('/api/cosmetics', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind, id }),
    });
    setBusy(null);
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      setErr(d.error ?? 'Failed');
      return;
    }
    router.refresh();
  }

  function btnLabel(kind: 'title' | 'frame', id: string, cost: number, equipped: boolean) {
    if (equipped) return 'Equipped';
    if (cost === 0 || unlocked.has(`${kind}:${id}`)) return 'Equip';
    return `${cost} 🪙`;
  }

  return (
    <div className="space-y-6">
      {err && <p className="text-center text-sm text-danger">{err}</p>}

      <section>
        <h3 className="mb-2 font-display font-bold">Fan Titles</h3>
        <div className="space-y-2">
          {FAN_TITLES.map((t) => {
            const equipped = fanTitle === t.id;
            return (
              <div key={t.id} className="flex items-center justify-between rounded-xl border border-white/10 bg-white/5 px-4 py-3">
                <span className="text-sm">{t.label}</span>
                <button
                  disabled={equipped || busy !== null}
                  onClick={() => equip('title', t.id)}
                  className={equipped ? 'stat-pill text-neon' : 'btn-neon text-xs'}
                >
                  {btnLabel('title', t.id, t.cost, equipped)}
                </button>
              </div>
            );
          })}
        </div>
      </section>

      <section>
        <h3 className="mb-2 font-display font-bold">Avatar Frames</h3>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {AVATAR_FRAMES.map((f) => {
            const equipped = frame === f.id;
            return (
              <div key={f.id} className="glass flex flex-col items-center gap-2 p-3">
                <div className={`flex h-12 w-12 items-center justify-center rounded-full bg-pitch-700 ${frameRing(f.id)}`}>⚽</div>
                <span className="text-center text-xs">{f.label}</span>
                <button
                  disabled={equipped || busy !== null}
                  onClick={() => equip('frame', f.id)}
                  className={equipped ? 'text-xs text-neon' : 'btn-ghost !px-3 !py-1 text-xs'}
                >
                  {btnLabel('frame', f.id, f.cost, equipped)}
                </button>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
