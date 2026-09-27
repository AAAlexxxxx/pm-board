/* PM Board service worker: app shell cache-first (versioned), data network-first with cache fallback. */
const V = 'pmboard-v1';
const SHELL = ['./', './index.html', './app.js', './style.css', './manifest.webmanifest',
  './icons/icon-180.png', './icons/icon-192.png', './icons/icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(V).then((c) => c.addAll(SHELL)).catch(() => {}).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k !== V).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (url.origin !== location.origin || e.request.method !== 'GET') return;
  if (url.pathname.includes('/data/')) {   // fresh numbers first, the last good copy when offline
    e.respondWith(fetch(e.request).then((r) => {
      if (r.ok) { const copy = r.clone(); caches.open(V).then((c) => c.put(e.request, copy)).catch(() => {}); }
      return r;
    }).catch(() => caches.match(e.request).catch(() => undefined)));
    return;
  }
  e.respondWith(caches.match(e.request, { ignoreSearch: true }).catch(() => undefined).then((hit) => {   // shell: stale-while-revalidate
    const net = fetch(e.request).then((r) => {
      if (r.ok) { const copy = r.clone(); caches.open(V).then((c) => c.put(e.request, copy)).catch(() => {}); }
      return r;
    }).catch(() => hit);
    return hit || net;
  }));
});
