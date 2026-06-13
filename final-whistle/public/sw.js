// Minimal service worker — required for PWA installability (and the TWA APK).
// Network-first so live match data stays fresh; falls back to cache offline.
const CACHE = 'final-whistle-v1';
const SHELL = ['/', '/dashboard', '/icon-192.png', '/icon-512.png'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const { request } = event;
  // Only handle GET navigations/assets; let API + auth pass straight through.
  if (request.method !== 'GET' || request.url.includes('/api/') || request.url.includes('/auth/')) {
    return;
  }
  event.respondWith(
    fetch(request)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(request, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(request).then((r) => r || caches.match('/')))
  );
});
