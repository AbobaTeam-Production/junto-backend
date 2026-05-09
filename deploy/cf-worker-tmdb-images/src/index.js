// Cloudflare Worker: public reverse-proxy for TMDb image CDN.
//
// Why: image.tmdb.org is unreachable from RU. Browser <img> tags
// can't carry a custom auth header, so we keep this Worker public
// (no shared secret). The poster paths come from TMDb already so
// they don't reveal anything new.
//
// Endpoint: https://tmdb-images-proxy.<account>.workers.dev/t/p/<size>/<path>
// Forwards to:  https://image.tmdb.org/t/p/<size>/<path>
//
// Smart Placement (see wrangler.toml) keeps the runtime on a non-RU
// edge so the upstream CloudFront sees a non-RU source IP. DoH +
// fetch-by-IP doesn't help on the free plan: CF returns
// `error code 1003 (direct IP access not allowed)`.

const TARGET = "https://image.tmdb.org";

// Long browser/CDN cache: TMDb image paths are content-hashed —
// a different artwork gets a different path, so safe to cache hard.
const CACHE_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
};

export default {
    async fetch(request) {
        const url = new URL(request.url);

        if (!url.pathname.startsWith("/t/p/")) {
            return new Response("not found", { status: 404 });
        }

        const upstreamReq = new Request(TARGET + url.pathname + url.search, {
            method: request.method,
            redirect: "follow",
        });

        try {
            // Only cache 2xx for a year — caching 403 baked us in earlier.
            const resp = await fetch(upstreamReq, {
                cf: {
                    cacheTtlByStatus: { "200-299": 31536000, "404": 60, "500-599": 0 },
                    cacheEverything: true,
                },
            });
            const headers = new Headers(resp.headers);
            if (resp.ok) {
                for (const [k, v] of Object.entries(CACHE_HEADERS)) {
                    headers.set(k, v);
                }
            } else {
                headers.set("Cache-Control", "no-store");
            }
            headers.set("Access-Control-Allow-Origin", "*");
            return new Response(resp.body, {
                status: resp.status,
                statusText: resp.statusText,
                headers,
            });
        } catch (err) {
            return new Response("upstream_failed: " + String(err), { status: 502 });
        }
    },
};
