import PenaltyGame from '@/components/PenaltyGame';

export const dynamic = 'force-dynamic';

export default function PlayPage() {
  return (
    <div className="space-y-4">
      <h1 className="font-display text-2xl font-bold">🥅 Penalty Shootout</h1>
      <p className="text-sm text-white/50">Earn 2 coins per goal. Best score counts for the Penalty Kings board.</p>
      <PenaltyGame />
    </div>
  );
}
