const CACHE_NAME = 'erp-cache-v2'; // Bumped to v2 to force an update
const STATIC_ASSETS = [
    '/',
    '/static/js/offline-sync.js', // Updated to match our new script
    // Add your local CSS/Bootstrap paths below if you have them:
    // '/static/css/style.css', 
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

// 3. Fetch Strategy: Network First for HTML, Cache First for Static
self.addEventListener('fetch', (e) => {
    if (e.request.method !== 'GET') return; // Don't cache POST/PUT requests

    const isHtmlRequest = e.request.headers.get('accept').includes('text/html');

    if (isHtmlRequest) {
        // Network First for pages
        e.respondWith(
            fetch(e.request)
                .then(res => {
                    const resClone = res.clone();
                    caches.open(CACHE_NAME).then(cache => cache.put(e.request, resClone));
                    return res;
                })
                .catch(async () => {
                    // Strip query parameters so /inventory?search=... falls back to cached /inventory
                    const url = new URL(e.request.url);
                    let cachedRes = await caches.match(url.pathname);
                    
                    if (cachedRes) {
                        return cachedRes;
                    }
                    
                    // Fallback response if the page was never cached and you are offline
                    return new Response(
                        '<!DOCTYPE html><html><head><title>Offline</title><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/css/bootstrap.min.css"></head><body class="p-5 text-center bg-light"><div class="card shadow p-4 mx-auto" style="max-width: 400px;"><h3 class="text-danger">You are offline</h3><p class="text-muted">This page has not been cached yet. Please visit it while online at least once.</p><a href="/" class="btn btn-primary mt-2">Go to Dashboard</a></div></body></html>',
                        { headers: { 'Content-Type': 'text/html' } }
                    );
                })
        );
    } else {
        // Cache First for CSS/JS/Images
        e.respondWith(
            caches.match(e.request).then(cachedRes => {
                return cachedRes || fetch(e.request).then(res => {
                    const resClone = res.clone();
                    caches.open(CACHE_NAME).then(cache => cache.put(e.request, resClone));
                    return res;
                }).catch(() => {
                    // Fallback for static assets if needed
                    return new Response('', { status: 404, statusText: 'Not Found' });
                });
            })
        );
    }
});