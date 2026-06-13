'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

const items = [
  { href: '/dashboard', icon: '🏟️', label: 'Home' },
  { href: '/leaderboard', icon: '🏆', label: 'Ranks' },
  { href: '/play', icon: '🥅', label: 'Play' },
  { href: '/leagues', icon: '👥', label: 'Leagues' },
  { href: '/profile', icon: '👤', label: 'You' },
];

export default function BottomNav() {
  const path = usePathname();
  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-white/10 bg-pitch-900/80 backdrop-blur-xl">
      <div className="mx-auto flex max-w-2xl items-stretch justify-between px-2">
        {items.map((it) => {
          const active = path === it.href || path.startsWith(it.href + '/');
          return (
            <Link
              key={it.href}
              href={it.href}
              className={`flex flex-1 flex-col items-center gap-0.5 py-2.5 text-xs transition ${
                active ? 'text-neon' : 'text-white/50 hover:text-white/80'
              }`}
            >
              <span className="text-lg">{it.icon}</span>
              {it.label}
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
