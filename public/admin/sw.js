/* Service worker for the offline register page only. It keeps a copy of the page's own files (no family data:
   that is kept encrypted by the page itself) and never caches API answers. */
const CACHE = "hah-offline-v2";
const SHELL = ["/admin/offline-register", "/admin/js/offline.js", "/admin/admin.css", "/css/fonts.css",
  "/img/logo.png", "/img/favicon.png", "/fonts/fredoka-latin.woff2", "/fonts/nunito-latin.woff2"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || !SHELL.includes(url.pathname)) return;
  // network first (so updates arrive), the saved copy when there's no connection
  e.respondWith(fetch(e.request).then(res => {
    if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(url.pathname, copy)); }
    return res;
  }).catch(() => caches.match(url.pathname)));
});
