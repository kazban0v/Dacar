// DACAR POS Service Worker
const CACHE_NAME = 'dacar-pos-v1';
const STATIC_ASSETS = [
    '/static/favicon.svg',
    '/static/icons/icon-192.png',
    '/static/icons/icon-512.png',
    '/static/icons/apple-touch-icon.png',
    '/static/manifest.json'
];

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(STATIC_ASSETS);
        })
    );
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((keys) => {
            return Promise.all(
                keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
            );
        })
    );
    self.clients.claim();
});

self.addEventListener('fetch', (event) => {
    // Network-first strategy for API and dynamic pages, cache-first for static icons
    if (event.request.url.includes('/static/icons/') || event.request.url.includes('/static/favicon.svg')) {
        event.respondWith(
            caches.match(event.request).then((cached) => {
                return cached || fetch(event.request);
            })
        );
    }
});
