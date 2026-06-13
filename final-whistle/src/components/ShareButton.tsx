'use client';
import { useState } from 'react';

export default function ShareButton({ predictionId }: { predictionId: string }) {
  const [copied, setCopied] = useState(false);

  async function share() {
    const url = `${window.location.origin}/share/${predictionId}`;
    const text = 'Check out my Final Whistle prediction! ⚽';
    if (navigator.share) {
      try {
        await navigator.share({ title: 'Final Whistle', text, url });
        return;
      } catch {
        /* user cancelled — fall through to copy */
      }
    }
    await navigator.clipboard.writeText(url);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <button onClick={share} className="btn-ghost w-full text-sm">
      {copied ? '✓ Link copied!' : '📤 Share prediction card'}
    </button>
  );
}
