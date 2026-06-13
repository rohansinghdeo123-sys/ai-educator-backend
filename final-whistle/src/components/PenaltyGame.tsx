'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';

// 30-second penalty shootout on a single <canvas>.
// Tap one of 5 aim zones; the keeper dives to a random zone. Beat the keeper to score.

const W = 360;
const H = 480;
const ROUND_SECONDS = 30;

// Aim zones across the goal mouth (canvas coords).
const ZONES = [
  { x: 70, y: 120 },
  { x: 145, y: 95 },
  { x: 180, y: 150 },
  { x: 215, y: 95 },
  { x: 290, y: 120 },
];

type Phase = 'ready' | 'playing' | 'over';

export default function PenaltyGame() {
  const router = useRouter();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [phase, setPhase] = useState<Phase>('ready');
  const [score, setScore] = useState(0);
  const [shots, setShots] = useState(0);
  const [timeLeft, setTimeLeft] = useState(ROUND_SECONDS);
  const [submitted, setSubmitted] = useState(false);
  const [coins, setCoins] = useState<number | null>(null);

  // Mutable game state held in a ref so the rAF loop stays stable.
  const game = useRef({
    phase: 'ready' as Phase,
    endAt: 0,
    keeperX: W / 2,
    keeperTargetX: W / 2,
    ball: null as null | { fromX: number; fromY: number; toX: number; toY: number; t: number; saved: boolean },
    flash: '' as string,
    flashUntil: 0,
  });

  const shoot = useCallback((zoneIndex: number) => {
    const g = game.current;
    if (g.phase !== 'playing' || g.ball) return;
    const target = ZONES[zoneIndex];
    const keeperZone = ZONES[Math.floor(Math.random() * ZONES.length)];
    const saved = Math.abs(keeperZone.x - target.x) < 45 && Math.random() < 0.85;
    g.keeperTargetX = keeperZone.x;
    g.ball = { fromX: W / 2, fromY: 420, toX: target.x, toY: target.y, t: 0, saved };
  }, []);

  // Map a canvas click to the nearest aim zone.
  const onCanvasPointer = useCallback(
    (e: React.PointerEvent<HTMLCanvasElement>) => {
      const canvas = canvasRef.current;
      if (!canvas || game.current.phase !== 'playing') return;
      const rect = canvas.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width) * W;
      const y = ((e.clientY - rect.top) / rect.height) * H;
      let best = 0;
      let bestD = Infinity;
      ZONES.forEach((z, i) => {
        const d = (z.x - x) ** 2 + (z.y - y) ** 2;
        if (d < bestD) {
          bestD = d;
          best = i;
        }
      });
      shoot(best);
    },
    [shoot]
  );

  function start() {
    const g = game.current;
    g.phase = 'playing';
    g.endAt = performance.now() + ROUND_SECONDS * 1000;
    g.ball = null;
    g.flash = '';
    setScore(0);
    setShots(0);
    setSubmitted(false);
    setCoins(null);
    setTimeLeft(ROUND_SECONDS);
    setPhase('playing');
  }

  // Submit score when the round ends.
  useEffect(() => {
    if (phase !== 'over' || submitted) return;
    setSubmitted(true);
    fetch('/api/shootout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ score }),
    })
      .then((r) => r.json())
      .then((d) => {
        setCoins(d.coins_awarded ?? 0);
        router.refresh();
      })
      .catch(() => setCoins(0));
  }, [phase, submitted, score, router]);

  // Render + game loop.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d')!;
    let raf = 0;

    const draw = (now: number) => {
      const g = game.current;

      // Time handling.
      if (g.phase === 'playing') {
        const remaining = Math.max(0, Math.ceil((g.endAt - now) / 1000));
        setTimeLeft(remaining);
        if (now >= g.endAt) {
          g.phase = 'over';
          setPhase('over');
        }
      }

      // Keeper easing toward target.
      g.keeperX += (g.keeperTargetX - g.keeperX) * 0.18;
      if (g.phase === 'playing' && !g.ball) {
        // idle bob between posts
        g.keeperTargetX = W / 2 + Math.sin(now / 400) * 60;
      }

      // Ball animation.
      if (g.ball) {
        g.ball.t += 0.06;
        if (g.ball.t >= 1) {
          const scored = !g.ball.saved;
          setShots((s) => s + 1);
          if (scored) setScore((s) => s + 1);
          g.flash = scored ? 'GOAL!' : 'SAVED!';
          g.flashUntil = now + 600;
          g.ball = null;
        }
      }

      // ---- DRAW ----
      ctx.clearRect(0, 0, W, H);
      // Pitch
      const grad = ctx.createLinearGradient(0, 0, 0, H);
      grad.addColorStop(0, '#0D1424');
      grad.addColorStop(1, '#0A2A1A');
      ctx.fillStyle = grad;
      ctx.fillRect(0, 0, W, H);

      // Goal frame
      ctx.strokeStyle = 'rgba(255,255,255,0.85)';
      ctx.lineWidth = 6;
      ctx.strokeRect(50, 70, 260, 130);
      // Net hint
      ctx.strokeStyle = 'rgba(255,255,255,0.12)';
      ctx.lineWidth = 1;
      for (let i = 60; i < 310; i += 16) {
        ctx.beginPath();
        ctx.moveTo(i, 72);
        ctx.lineTo(i, 198);
        ctx.stroke();
      }

      // Aim zone hints (only while playing & idle)
      if (g.phase === 'playing' && !g.ball) {
        ZONES.forEach((z) => {
          ctx.beginPath();
          ctx.arc(z.x, z.y, 13, 0, Math.PI * 2);
          ctx.fillStyle = 'rgba(57,255,139,0.18)';
          ctx.fill();
          ctx.strokeStyle = 'rgba(57,255,139,0.6)';
          ctx.lineWidth = 2;
          ctx.stroke();
        });
      }

      // Keeper
      ctx.fillStyle = '#FFD45E';
      ctx.fillRect(g.keeperX - 22, 120, 44, 70);
      ctx.beginPath();
      ctx.arc(g.keeperX, 112, 13, 0, Math.PI * 2);
      ctx.fill();

      // Penalty spot
      ctx.fillStyle = 'rgba(255,255,255,0.5)';
      ctx.beginPath();
      ctx.arc(W / 2, 430, 4, 0, Math.PI * 2);
      ctx.fill();

      // Ball
      let bx = W / 2;
      let by = 420;
      if (g.ball) {
        const e = g.ball.t;
        bx = g.ball.fromX + (g.ball.toX - g.ball.fromX) * e;
        by = g.ball.fromY + (g.ball.toY - g.ball.fromY) * e;
      }
      const r = g.ball ? 8 - g.ball.t * 3 : 9;
      ctx.beginPath();
      ctx.arc(bx, by, r, 0, Math.PI * 2);
      ctx.fillStyle = '#fff';
      ctx.fill();
      ctx.strokeStyle = '#0A0E1A';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // Flash text
      if (now < g.flashUntil) {
        ctx.fillStyle = g.flash === 'GOAL!' ? '#39FF8B' : '#FF5C7A';
        ctx.font = 'bold 40px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(g.flash, W / 2, 260);
      }

      raf = requestAnimationFrame(draw);
    };

    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="stat-pill">⏱️ {timeLeft}s</div>
        <div className="stat-pill text-neon">⚽ {score} goals</div>
        <div className="stat-pill">🎯 {shots} shots</div>
      </div>

      <div className="glass relative overflow-hidden p-2">
        <canvas
          ref={canvasRef}
          width={W}
          height={H}
          onPointerDown={onCanvasPointer}
          className="mx-auto block w-full max-w-[360px] cursor-pointer touch-none rounded-xl"
        />

        {phase !== 'playing' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center bg-pitch-950/70 backdrop-blur-sm">
            {phase === 'ready' ? (
              <>
                <h2 className="font-display text-2xl font-bold">Penalty Shootout</h2>
                <p className="mt-2 max-w-xs text-center text-sm text-white/60">
                  Tap a glowing zone to shoot. Beat the keeper. Score as many as you can in 30s.
                </p>
                <button onClick={start} className="btn-neon mt-5">
                  Start shooting
                </button>
              </>
            ) : (
              <>
                <h2 className="font-display text-3xl font-bold text-neon">{score} goals!</h2>
                <p className="mt-1 text-sm text-white/60">
                  {coins == null ? 'Saving…' : `+${coins} coins earned 🪙`}
                </p>
                <button onClick={start} className="btn-neon mt-5">
                  Play again
                </button>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
