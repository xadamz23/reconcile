// Rendered by app/routes/pwa.py. VERSION changes whenever any static asset changes.
const VERSION = "{{ version }}";
const CACHE = `reconcile-${VERSION}`;
const PRECACHE = {{ precache | safe }};
// Pages saved for reading offline. Not versioned, so they outlive an update (their old
// asset URLs fall back to the current files, below).
const PAGES = "reconcile-pages";
const SAVED = new Set(["/", "/register", "/bills", "/spending", "/reconcile"]);

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(PRECACHE)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE && k !== PAGES).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;

  // Hashed static assets never change: cache first.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res.ok && url.searchParams.has("v")) {
          const copy = res.clone();
          caches.open(CACHE).then((cache) => cache.put(req, copy));
        }
        return res;
      }).catch(() => caches.match(req, { ignoreSearch: true }))) // a saved page's older asset hash
    );
    return;
  }

  // Pages: always go to the network so balances are never stale, saving a copy of each
  // main page. Offline, serve that copy (app.js marks it read-only), or /offline. So too
  // when Cloudflare answers for a server it can't reach (502-504, its 52x, the tunnel's
  // 530); a 500 is the app's own error and is shown as it is.
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).then((res) => {
      if (res.status >= 502) return savedPage(url.pathname, "down");
      // Not a redirect: an expired Cloudflare Access login must never be saved as a page.
      // Not a search or a filter (?q=…, ?period=…), so the copy is the plain page.
      if (res.ok && !res.redirected && res.type === "basic" && !url.search && SAVED.has(url.pathname)) {
        event.waitUntil(savePage(url.pathname, res.clone()));
      }
      return res;
    }).catch(() => savedPage(url.pathname, "offline")));
  }
});

async function savePage(path, res) {
  const body = await res.blob();
  const headers = { "Content-Type": res.headers.get("Content-Type"), "X-Saved-At": String(Date.now()) };
  const cache = await caches.open(PAGES);
  await cache.put(path, new Response(body, { headers }));
}

// The saved copy, or /offline, marked for app.js: <html data-offline> says why ("offline"
// or "down", the server can't be reached) and data-saved-at when the copy was saved.
async function savedPage(path, why) {
  const hit = (await (await caches.open(PAGES)).match(path)) || (await caches.match("/offline"));
  if (!hit) return Response.error();
  const saved = hit.headers.get("X-Saved-At");
  const attrs = `data-offline="${why}"` + (saved ? ` data-saved-at="${saved}"` : "");
  const html = (await hit.text()).replace("<html", `<html ${attrs}`);
  return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8" } });
}
