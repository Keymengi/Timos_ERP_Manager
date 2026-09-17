const CACHE_NAME = 'erp-cache-v4'; // Bumped to v4: added more pages/assets to the precache list below
const STATIC_ASSETS = [
    // Page shells — cached on install so each module still opens offline
    // even before the user has visited it once. (The fetch handler below
    // also caches every page as it's actually visited, so this list just
    // covers the "never been online on this device yet" case.)
    '/',
    '/customers',
    '/sales',
    '/debts',
    '/payments',
    '/inventory',
    '/quotations',
    '/bookings',
    '/loans',
    '/reports',
    '/analytics',

    // Static assets — CSS, JS, fonts, manifest, favicon
    '/static/css/bootstrap.min.css',
    '/static/css/style.css',
    '/static/js/bootstrap.bundle.min.js',
    '/static/js/table-search.js',
    '/static/js/offline-sync.js',
    '/static/manifest.json',
    '/static/favicon.svg',
    '/static/fonts/ibm-plex-mono-400.woff2',
    '/static/fonts/ibm-plex-mono-500.woff2',
    '/static/fonts/ibm-plex-mono-600.woff2',
    '/static/fonts/ibm-plex-sans-400.woff2',
    '/static/fonts/ibm-plex-sans-500.woff2',
    '/static/fonts/ibm-plex-sans-600.woff2',
    '/static/fonts/zilla-slab-500.woff2',
    '/static/fonts/zilla-slab-600.woff2',
    '/static/fonts/zilla-slab-700.woff2',
];

// 1. Install & Cache Static Assets
//
// Uses individual cache.add() calls instead of cache.addAll() so that one
// failing request doesn't abort the whole precache. This matters here
// because several of the page shells above (e.g. /reports, /analytics) are
// Admin-only — a Staff account, or anyone not logged in yet, would get a
// redirect/403 for those specific URLs, and addAll() would have thrown on
// that single failure and left EVERYTHING (including the plain static
// files) uncached. Promise.allSettled lets every request succeed or fail
// independently; whatever succeeds gets cached, and failures are just
// logged.
self.addEventListener('install', (e) => {
    e.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return Promise.allSettled(
                STATIC_ASSETS.map((url) =>
                    cache.add(url).catch((err) => {
                        console.warn('[SW] Skipped precaching', url, err);
                    })
                )
            );
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
