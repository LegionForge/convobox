// Minimal service worker for ConvoBox's web UI -- exists only to satisfy
// PWA install criteria (some mobile browsers gate the install prompt on a
// registered SW) and to keep the app shell available if the loopback
// server is briefly unreachable. Deliberately does NOT cache anything
// under /api/ or /health -- that's live session state (transcripts,
// approvals, settings), never something a stale cache should ever answer
// instead of a real request. Not wired into index.html yet -- see
// docs/field-notes/2026-07-28-other-claude-code-web-uis-dont-transfer-much.md.

"use strict";

const CACHE_NAME = "convobox-shell-v1";
const SHELL_URLS = ["./", "./manifest.json", "./img/legionforge-logo.svg"];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_URLS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names
          .filter((name) => name !== CACHE_NAME)
          .map((name) => caches.delete(name))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  // Never intercept the API or the SSE stream -- always go straight to
  // the real, live loopback server. This server has no auth (see
  // WEB-UI-USAGE.md's security section); a service worker's cache is not
  // the place to introduce a second, stale copy of anything session state.
  if (url.pathname.startsWith("/api/") || url.pathname === "/health") {
    return;
  }
  if (event.request.method !== "GET") {
    return;
  }
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, copy));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});
