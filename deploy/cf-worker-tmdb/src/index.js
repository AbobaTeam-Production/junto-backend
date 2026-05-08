// Cloudflare Worker: transparent reverse-proxy for TMDb.
//
// Why: api.themoviedb.org is unreachable from RU. Worker sits in
// CF's edge (RU-accessible) and forwards to TMDb on the user's
// behalf. Same pattern as gemini-proxy in the twitch_clips repo.
//
// Endpoint: https://tmdb-proxy.<account>.workers.dev/3/<path>
// Forwards to:  https://api.themoviedb.org/3/<path>
//
// Auth: caller MUST send `X-Proxy-Secret: <SHARED_SECRET>`. Without
// it → 401. Secret set via `wrangler secret put SHARED_SECRET`.
//
// The TMDb v3 API key (api_key= query param) is supplied by the
// caller, NOT the Worker — that lets us rotate keys without
// redeploying.

const TARGET = "https://api.themoviedb.org";

export default {
    async fetch(request, env) {
        const supplied = request.headers.get("X-Proxy-Secret");
        if (!env.SHARED_SECRET || supplied !== env.SHARED_SECRET) {
            return new Response("forbidden", { status: 401 });
        }

        const url = new URL(request.url);
        const upstreamUrl = TARGET + url.pathname + url.search;

        const headers = new Headers(request.headers);
        headers.delete("X-Proxy-Secret");
        headers.delete("Host");
        headers.set("Host", "api.themoviedb.org");

        const upstreamReq = new Request(upstreamUrl, {
            method: request.method,
            headers,
            body: request.method === "GET" || request.method === "HEAD"
                ? undefined
                : request.body,
            redirect: "follow",
        });

        try {
            const resp = await fetch(upstreamReq);
            // Pass through the body + status, let CF cache GETs by
            // default (TMDb already sets short Cache-Control on most
            // endpoints; we don't override).
            return new Response(resp.body, {
                status: resp.status,
                statusText: resp.statusText,
                headers: resp.headers,
            });
        } catch (err) {
            return new Response(
                JSON.stringify({ error: "upstream_failed", message: String(err) }),
                { status: 502, headers: { "Content-Type": "application/json" } },
            );
        }
    },
};
