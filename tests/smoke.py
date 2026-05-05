#!/usr/bin/env python3
"""
Junto smoke tests — runs API + WS probes against a live deployment to catch
the kind of regressions that bit us during dev (mixed-content URLs, stale
Redis cache, broken HLS playlists, etc.) before the user does.

Usage:
    python scripts/smoke.py --base https://junto.local:8443
    python scripts/smoke.py --base http://192.168.0.50:8080      # native path

Run as part of CI or after every `flutter build web` / docker compose up.
Exits non-zero on any failure; prints a one-line summary per check.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import ssl
import sys
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import httpx

try:
    import websockets
except ImportError:
    print("ERROR: pip install websockets httpx", file=sys.stderr)
    sys.exit(2)


@dataclass
class Result:
    name: str
    ok: bool
    detail: str = ""

    def __str__(self) -> str:
        mark = "PASS" if self.ok else "FAIL"
        line = f"[{mark}] {self.name}"
        if self.detail:
            line += f" — {self.detail}"
        return line


class Probe:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        # Browser will refuse to use untrusted certs; smoke just verifies the
        # API itself, so we accept self-signed.
        self._http = httpx.AsyncClient(verify=False, timeout=15)
        parsed = urlparse(self.base)
        self.is_https = parsed.scheme == "https"
        self.host = parsed.netloc
        self.expected_ws_scheme = "wss" if self.is_https else "ws"
        self.token: Optional[str] = None
        self.user_id: Optional[int] = None
        self.results: list[Result] = []

    def add(self, name: str, ok: bool, detail: str = ""):
        r = Result(name, ok, detail)
        self.results.append(r)
        print(r)

    async def close(self):
        await self._http.aclose()

    # ---- checks ----

    async def check_time(self):
        try:
            r = await self._http.get(f"{self.base}/api/time/")
            data = r.json()
            ts = int(data["server_time"])
            drift = abs(ts - int(time.time() * 1000))
            self.add("/api/time/ reachable", r.status_code == 200,
                     f"HTTP {r.status_code}, drift={drift}ms")
            self.add("/api/time/ drift sane", drift < 60_000,
                     f"{drift}ms vs local")
        except Exception as e:
            self.add("/api/time/ reachable", False, str(e)[:120])

    async def check_guest_login(self):
        try:
            r = await self._http.post(f"{self.base}/api/auth/guest/")
            data = r.json()
            self.token = data["tokens"]["access"]
            self.user_id = data["user"]["id"]
            self.add("guest login", True, f"user_id={self.user_id}")
        except Exception as e:
            self.add("guest login", False, str(e)[:120])

    async def check_torrent_search(self, query: str = "matrix"):
        if not self.token:
            return
        try:
            r = await self._http.get(
                f"{self.base}/api/media/torrent/search/",
                params={"q": query},
                headers={"Authorization": f"Bearer {self.token}"},
            )
            items = r.json()
            self.add(
                f"torrent search '{query}'",
                isinstance(items, list) and len(items) > 0,
                f"{len(items) if isinstance(items, list) else 'n/a'} results",
            )
            if isinstance(items, list) and items:
                first = items[0]
                self.add(
                    "search result has inline magnet",
                    bool(first.get("magnet")),
                    first.get("title", "")[:60],
                )
        except Exception as e:
            self.add(f"torrent search '{query}'", False, str(e)[:120])

    async def check_room_create_and_token(self):
        if not self.token:
            return
        # Create a throwaway room so we can probe livekit-token + WS.
        try:
            r = await self._http.post(
                f"{self.base}/api/rooms/create/",
                headers={"Authorization": f"Bearer {self.token}"},
            )
            room_id = r.json()["room_id"]
            self.add("create room", True, room_id)
        except Exception as e:
            self.add("create room", False, str(e)[:120])
            return

        # LiveKit token: critical mixed-content check.
        try:
            r = await self._http.get(
                f"{self.base}/api/rooms/{room_id}/livekit-token/",
                headers={"Authorization": f"Bearer {self.token}"},
            )
            data = r.json()
            url = data.get("url", "")
            scheme = urlparse(url).scheme
            self.add(
                "livekit token returned",
                bool(data.get("token")) and bool(url),
                f"url={url}",
            )
            self.add(
                "livekit ws scheme matches client scheme",
                scheme == self.expected_ws_scheme,
                f"got {scheme}, expected {self.expected_ws_scheme} "
                "(mixed-content blocker if wrong)",
            )
        except Exception as e:
            self.add("livekit token returned", False, str(e)[:120])

        # WS handshake — verify auth + room access work end-to-end.
        ws_scheme = "wss" if self.is_https else "ws"
        ws_url = f"{ws_scheme}://{self.host}/ws/room/{room_id}/?token={self.token}"
        try:
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            async with websockets.connect(
                ws_url, ssl=ssl_ctx if self.is_https else None
            ) as ws:
                # Expect at least a state_sync or online_users frame promptly.
                msg = await asyncio.wait_for(ws.recv(), timeout=5)
                event = json.loads(msg).get("event")
                self.add(
                    "room WS connected",
                    event in {"state_sync", "online_users", "play_media",
                              "user_joined"},
                    f"first event = {event}",
                )
        except Exception as e:
            self.add("room WS connected", False, str(e)[:120])

    async def check_hls_playlist_health(self):
        """If there's any ready torrent media in the system, fetch its HLS
        playlist and check it doesn't have the bad shapes we hit (stray
        DISCONTINUITY in the first record, missing EXT-X-MAP, etc.)."""
        if not self.token:
            return
        try:
            r = await self._http.get(
                f"{self.base}/api/rooms/",
                headers={"Authorization": f"Bearer {self.token}"},
            )
            rooms = r.json() if r.status_code == 200 else []
            # Find any media entry with hls_url
            sample_url = None
            for room in rooms:
                for m in room.get("media", []) or []:
                    hls = m.get("hls_url")
                    if hls and m.get("status") == "ready":
                        sample_url = hls
                        break
                if sample_url:
                    break
            if not sample_url:
                self.add("HLS playlist health", True, "(no ready HLS media — skipped)")
                return
            # hls_url is host-relative or absolute
            full = sample_url if sample_url.startswith("http") else f"{self.base}{sample_url}"
            r = await self._http.get(full)
            playlist = r.text
            head_lines = playlist.splitlines()[:6]
            # Bad: discontinuity before first segment
            bad_disc = bool(re.search(
                r"#EXT-X-DISCONTINUITY\s*\n#EXTINF",
                "\n".join(head_lines)
            )) or any(
                line.strip() == "#EXT-X-DISCONTINUITY"
                and i < 6
                and not any(
                    re.match(r"^seg_|^.+\.m4s|^.+\.ts",
                             head_lines[j].strip())
                    for j in range(i)
                )
                for i, line in enumerate(head_lines)
            )
            has_map = "#EXT-X-MAP" in playlist
            self.add(
                "HLS playlist has #EXT-X-MAP",
                has_map,
                "fmp4 init segment must be referenced",
            )
            self.add(
                "HLS no stray DISCONTINUITY at top",
                not bad_disc,
                "first 6 lines: " + " | ".join(l[:30] for l in head_lines[:6]),
            )
        except Exception as e:
            self.add("HLS playlist health", False, str(e)[:120])

    async def run(self):
        print(f"--- smoke against {self.base} ---")
        await self.check_time()
        await self.check_guest_login()
        await self.check_torrent_search()
        await self.check_room_create_and_token()
        await self.check_hls_playlist_health()
        await self.close()
        failed = sum(1 for r in self.results if not r.ok)
        print(f"--- {failed} failed, {len(self.results) - failed} passed ---")
        return failed


async def main_async():
    p = argparse.ArgumentParser()
    p.add_argument("--base", required=True,
                   help="Base URL, e.g. https://junto.local:8443 or http://192.168.0.50:8080")
    args = p.parse_args()
    rc = await Probe(args.base).run()
    sys.exit(0 if rc == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main_async())
