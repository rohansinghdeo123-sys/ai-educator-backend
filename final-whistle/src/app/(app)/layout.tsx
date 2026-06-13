import Link from 'next/link';
import BottomNav from '@/components/BottomNav';

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto min-h-dvh max-w-2xl px-4 pb-24 pt-5">
      <header className="mb-5 flex items-center justify-between">
        <Link href="/dashboard" className="font-display text-xl font-extrabold">
          Final <span className="text-neon">Whistle</span>
        </Link>
      </header>
      {children}
      <BottomNav />
    </div>
  );
}
