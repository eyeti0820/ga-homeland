/* 1102-B: PWA壳缓存 — SWR(缓存秒回+后台更新), 主人手机经 ts.net(HTTPS) 访问, secure context 下 SW 可用
 * 策略: 壳(HTML/manifest/icons)=SWR 秒开+后台静默更新; /api/* 与 /media/* 网络直通(数据须新鲜, 媒体体积大防爆缓存)
 * ⚠前端改动须 bump CACHE_V(activate 时自动清旧); 非 GET 一律直通
 * 注: 路径双写法兼容(手机 tailscale /chat/ 前缀 与 本机 127 直访无前缀)
 */
const CACHE_V = 'chat-v1102b';
const PASS = p => p.startsWith('/api/') || p.startsWith('/chat/api/') || p.startsWith('/media/') || p.startsWith('/chat/media/');

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_V)
    .then(c => c.addAll(['./', './manifest.json', './icons/192.png', './icons/512.png']))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== CACHE_V).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const u = new URL(e.request.url);
  if (u.origin !== self.location.origin || PASS(u.pathname)) return;
  e.respondWith((async () => {
    const c = await caches.open(CACHE_V);
    const hit = await c.match(e.request);
    const net = fetch(e.request, { cache: 'no-cache' })   /* 协商式: Etag 304 快, 不吃启发式旧缓存 */
      .then(r => { if (r && r.ok) c.put(e.request, r.clone()); return r; })
      .catch(() => null);
    return hit || (await net) || new Response('offline & no cache', { status: 504, statusText: 'offline' });
  })());
});
