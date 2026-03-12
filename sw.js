// Dubai Strike Monitor — Service Worker
// Handles push notifications and basic caching

const CACHE_NAME = 'dsm-v1';
const ASSETS = ['./index.html', './manifest.json'];

// ── Install: cache shell ──────────────────────────────────────────────────
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME).then(c => c.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// ── Fetch: network-first for data.json, cache-first for assets ───────────
self.addEventListener('fetch', e => {
  if (e.request.url.includes('data.json')) return; // always fresh
  e.respondWith(
    caches.match(e.request).then(r => r || fetch(e.request))
  );
});

// ── Push notification received ────────────────────────────────────────────
self.addEventListener('push', e => {
  let payload = { title: 'New event', body: 'UAE area', type: 'security_alert', id: null };
  try { payload = e.data.json(); } catch (_) {}

  const TYPE_EMOJI = {
    impact_major:   '⚡',
    impact_minor:   '⚠️',
    interception:   '🛡️',
    security_alert: '🔔',
  };

  const emoji = TYPE_EMOJI[payload.type] || '🔔';
  const options = {
    body: payload.body || payload.location || 'UAE',
    icon: './icons/icon-192.png',
    badge: './icons/icon-192.png',
    tag: payload.id || 'dsm',
    renotify: true,
    data: { url: payload.url || './', id: payload.id },
    vibrate: [200, 100, 200],
    requireInteraction: false,
  };

  e.waitUntil(
    self.registration.showNotification(`${emoji} ${payload.title}`, options)
  );
});

// ── Notification click: open/focus the app ────────────────────────────────
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const targetUrl = (e.notification.data && e.notification.data.url) || './';
  e.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      for (const client of list) {
        if (client.url.includes('DubaiStrikes') && 'focus' in client)
          return client.focus();
      }
      return clients.openWindow(targetUrl);
    })
  );
});
