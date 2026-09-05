const CACHE_NAME = 'erp-cache-v3'; // Bumped to v3 to force everyone's browser to drop the old cache
const STATIC_ASSETS = [
    '/',
    '/static/js/offline-sync.js',
];

// 1. Install & Cache Static Assets
self.addEventListener('install', (e) => {
    e.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(STATIC_ASSETS);
        })
    );
    self.skipWaiting();
});

// 2. Clean up old caches
self.addEventListener('activate', (e) => {
    e.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))
            );
        })
    );
    self.clients.claim();
});

// 3. Fetch Strategy: Network First for EVERYTHING, cache is only a fallback.
//
// Previously, CSS/JS/images used "Cache First" — once a file was saved, the
// service worker would keep serving that saved copy forever and never check
// for a newer one, even after a hard refresh. That's why style updates kept
// reverting. Now every request tries the real network first, and only falls
// back to the saved copy if the network is unavailable (offline) — so the
// app still works offline, but online it always shows the latest version.
self.addEventListener('fetch', (e) => {
    if (e.request.method !== 'GET') return; // Don't cache POST/PUT requests

    const isHtmlRequest = e.request.headers.get('accept').includes('text/html');

    e.respondWith(
        fetch(e.request)
            .then(res => {
                const resClone = res.clone();
                caches.open(CACHE_NAME).then(cache => cache.put(e.request, resClone));
                return res;
            })
            .catch(async () => {
                // Offline fallback: try the exact URL first
                let cachedRes = await caches.match(e.request);
                if (cachedRes) return cachedRes;

                if (isHtmlRequest) {
                    // Strip query parameters so /inventory?search=... falls back to cached /inventory
                    const url = new URL(e.request.url);
                    cachedRes = await caches.match(url.pathname);
                    if (cachedRes) return cachedRes;

                    return new Response(
                        '<!DOCTYPE html><html><head><title>Offline</title><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/css/bootstrap.min.css"></head><body class="p-5 text-center bg-light"><div class="card shadow p-4 mx-auto" style="max-width: 400px;"><h3 class="text-danger">You are offline</h3><p class="text-muted">This page has not been cached yet. Please visit it while online at least once.</p><a href="/" class="btn btn-primary mt-2">Go to Dashboard</a></div></body></html>',
                        { headers: { 'Content-Type': 'text/html' } }
                    );
                }

                return new Response('', { status: 404, statusText: 'Not Found' });
            })
    );
});
