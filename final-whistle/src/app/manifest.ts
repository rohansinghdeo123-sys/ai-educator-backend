import type { MetadataRoute } from 'next';

// Web app manifest — makes Final Whistle installable on Android/iOS
// ("Add to Home Screen") and is the basis for the TWA/Bubblewrap APK.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'Final Whistle',
    short_name: 'Final Whistle',
    description: 'Daily football predictions and a 30-second penalty shootout.',
    start_url: '/dashboard',
    display: 'standalone',
    orientation: 'portrait',
    background_color: '#0A0E1A',
    theme_color: '#0A0E1A',
    categories: ['games', 'sports'],
    icons: [
      { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
      { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
      { src: '/icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
  };
}
