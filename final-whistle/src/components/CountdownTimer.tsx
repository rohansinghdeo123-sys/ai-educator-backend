'use client';
import { useEffect, useState } from 'react';
import { timeUntil } from '@/lib/format';

export default function CountdownTimer({ to, className }: { to: string; className?: string }) {
  const [state, setState] = useState(() => timeUntil(to));

  useEffect(() => {
    const id = setInterval(() => setState(timeUntil(to)), 1000);
    return () => clearInterval(id);
  }, [to]);

  return (
    <span className={className}>
      {state.live ? <span className="text-danger">● LIVE</span> : state.label}
    </span>
  );
}
