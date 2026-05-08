// Cloudflare Worker: public reverse-proxy for TMDb image CDN.
//
// Why: image.tmdb.org is unreachable from RU. Browser <img> tags
// can't carry a custom auth header, so we keep this Worker public
// (no shared secret). The poster paths come from TMDb already so
// they don't reveal anything new.
//
// Endpoint: https://tmdb-images-proxy.<account>.workers.dev/t/p/<size>/<path>
// Forwards to:  https://image.tmdb.org/t/p/<size>/<path>

const TARGET = "https://image.tmdb.org";

// Long browser/CDN cache: TMDb image paths are content-hashed —
// a different artwork gets a different path, so safe to cache hard.
const CACHE_HEADERS = {
    "Cache-Control": "public, max-age=31536000, immutable",
};

export default {
    async fetch(request) {
        const url = new URL(request.url);

        // Light path-shape check — don't proxy anything other than the
        // actual TMDb image namespace.
        if (!url.pathname.startsWith("/t/p/")) {
            return new Response("not found", { status: 404 });
        }

        const upstreamReq = new Request(TARGET + url.pathname + url.search, {
            method: request.method,
            redirect: "follow",
        });

        try {
            const resp = await fetch(upstreamReq, {
                cf: { cacheTtl: 31536000, cacheEverything: true },
            });
            const headers = new Headers(resp.headers);
            for (const [k, v] of Object.entries(CACHE_HEADERS)) {
                headers.set(k, v);
            }
            // Cross-origin fetch from app — be permissive.
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
