// Cosmetic catalog. Unlocks are tracked via coin_transactions (reason "unlock:<id>")
// so no extra table is needed. Once unlocked, re-equipping is free.

export interface Cosmetic {
  id: string;
  label: string;
  cost: number;
}

export const FAN_TITLES: Cosmetic[] = [
  { id: 'Rookie Fan', label: 'Rookie Fan', cost: 0 },
  { id: 'Terrace Regular', label: 'Terrace Regular', cost: 150 },
  { id: 'Ultra', label: 'Ultra', cost: 400 },
  { id: 'Touchline General', label: 'Touchline General', cost: 800 },
  { id: 'Legend of the Stand', label: 'Legend of the Stand', cost: 1500 },
];

// Frame id maps to a Tailwind ring/shadow class used by <AvatarFrame>.
export const AVATAR_FRAMES: { id: string; label: string; cost: number; ring: string }[] = [
  { id: 'none', label: 'None', cost: 0, ring: 'ring-0' },
  { id: 'neon', label: 'Neon Pitch', cost: 200, ring: 'ring-2 ring-neon shadow-neon' },
  { id: 'gold', label: 'Golden Boot', cost: 600, ring: 'ring-2 ring-gold shadow-gold' },
  { id: 'captain', label: "Captain's Armband", cost: 1000, ring: 'ring-4 ring-danger' },
];

export function frameRing(id: string): string {
  return AVATAR_FRAMES.find((f) => f.id === id)?.ring ?? 'ring-0';
}

export function findCosmetic(kind: 'title' | 'frame', id: string): Cosmetic | undefined {
  return (kind === 'title' ? FAN_TITLES : AVATAR_FRAMES).find((c) => c.id === id);
}
