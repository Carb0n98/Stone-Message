self.addEventListener('install', function(event) {
    event.waitUntil(
        caches.open('stone-cache-v2').then(function(cache) {
            return cache.addAll([
                '/',
                '/static/css/themes.css',
                '/static/css/style.css',
                '/static/js/script.js',
                '/static/icons/icon-192x192.png',
                '/static/icons/icon-512x512.png'
            ]);
        })
    );
    self.skipWaiting();
});

self.addEventListener('activate', function(event) {
    event.waitUntil(
        caches.keys().then(function(cacheNames) {
            return Promise.all(
                cacheNames.filter(function(name) {
                    return name !== 'stone-cache-v2';
                }).map(function(name) {
                    return caches.delete(name);
                })
            );
        })
    );
    self.clients.claim();
});

self.addEventListener('fetch', function(event) {
    var url = new URL(event.request.url);

    // Network-first for API calls and dynamic pages
    if (url.pathname.startsWith('/api/') ||
        url.pathname === '/' ||
        url.pathname.startsWith('/login') ||
        url.pathname.startsWith('/register') ||
        url.pathname.startsWith('/admin')) {
        event.respondWith(
            fetch(event.request).catch(function() {
                return caches.match(event.request);
            })
        );
        return;
    }

    // Cache-first for static assets
    event.respondWith(
        caches.match(event.request).then(function(response) {
            return response || fetch(event.request).then(function(fetchResponse) {
                // Cache new static assets
                if (url.pathname.startsWith('/static/') || url.pathname.startsWith('/uploads/')) {
                    var responseClone = fetchResponse.clone();
                    caches.open('stone-cache-v2').then(function(cache) {
                        cache.put(event.request, responseClone);
                    });
                }
                return fetchResponse;
            });
        })
    );
});

self.addEventListener('push', function(event) {
    var data = {};
    try {
        data = event.data.json();
    } catch (e) {
        data = { username: 'Stone Messages', content: event.data.text() };
    }
    var options = {
        body: data.content || 'Nova mensagem',
        icon: '/static/icons/icon-192x192.png',
        badge: '/static/icons/icon-192x192.png',
        tag: 'room-' + (data.room_id || 'general'),
        renotify: true,
        data: {
            room_id: data.room_id,
            url: '/'
        }
    };
    event.waitUntil(
        self.registration.showNotification(data.username || 'Stone Messages', options)
    );
});

self.addEventListener('notificationclick', function(event) {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: 'window' }).then(function(clientList) {
            for (var i = 0; i < clientList.length; i++) {
                if (clientList[i].url.includes('/') && 'focus' in clientList[i]) {
                    return clientList[i].focus();
                }
            }
            return clients.openWindow('/');
        })
    );
});