# junto-backend

Django + Daphne backend for [Junto](https://juntoapp.tech) — synchronous group movie watching with voice chat. Powers the API, WebSocket sync, media pipeline (HLS transcode + torrent ingest + rutube extractor), LiveKit voice SFU, and FCM push delivery.

This is also the entry point for **self-hosting** your own Junto instance. See below.

## Stack

| Service | What it does | Where in the repo |
|---|---|---|
| Django + DRF | REST API | `backend/apps/*` |
| Channels + Daphne | Room presence, sync, chat WebSocket | `backend/apps/rooms/consumers.py` |
| Celery + Beat | HLS transcode, push fan-out, scheduled cleanup | `backend/apps/*/tasks.py` |
| Postgres 16 | App database | docker volume `pgdata` |
| Redis 7 | Channels layer + Celery broker + Django cache | docker volume `redisdata` |
| LiveKit SFU | Voice/video transport | `livekit/livekit.yaml` |
| jacred | Torrent search shim | external `https://jac.red` by default |
| TorrServer | HTTP streaming for magnet sources | docker volume `torrserver-*` |
| nginx | TLS termination + same-origin reverse proxy | `nginx/nginx.conf`, `deploy/vps/nginx/conf.d/` |
| CF Workers | TMDb proxies (geo bypass) | `deploy/cf-worker-tmdb/`, `deploy/cf-worker-tmdb-images/` |

## Repo layout

```
backend/                  Django project root
  config/                 settings, urls, asgi
  apps/
    users/                auth + profile + tier
    social/               friends, devices, watch history
    rooms/                room CRUD + websocket consumer
    media_content/        upload, torrent, youtube/rutube, transcode tasks
    movies/               TMDb catalog, recs feed, mood lists
    billing/              subscription plans + mock checkout
  Dockerfile / Dockerfile.backend    CUDA build (dev) / slim (prod)

deploy/
  vps/                    production docker-compose, nginx, deploy script
  cf-worker-tmdb*/        Cloudflare Worker source for the TMDb proxies

nginx/                    dev nginx (mkcert + self-signed)
livekit/                  livekit.yaml dev config
docker-compose.yml        dev stack — talks to ./backend bind-mount
```

## Local development

```bash
cd junto-backend
cp deploy/vps/.env.example .env
$EDITOR .env                       # at minimum: SECRET_KEY, POSTGRES_PASSWORD, LIVEKIT_*
docker compose up -d --build       # uses ./backend bind-mount + Dockerfile (CUDA, GPU transcode)
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py seed_plans      # billing tariffs
docker compose exec backend python manage.py seed_movies     # 100+ catalog titles
```

The dev stack publishes nginx on `http://localhost:8080` and `https://junto.local:8443` (mkcert root needs to be trusted by the host browser).

`Dockerfile.backend` is the slim image used by CI for prod deploys; `Dockerfile` is the heavy CUDA build the dev box uses for nvenc transcoding.

## CI / deploy pipeline

| Trigger | Workflow | Result |
|---|---|---|
| Push to `main` | `.github/workflows/docker.yml` | `ghcr.io/abobateam-production/junto-backend:main` rebuilt |
| Run `deploy.sh` on VPS | `deploy/vps/deploy.sh` | Pull → up → migrate → seed_plans → collectstatic → nginx reload |

## Self-hosting

You can run your own Junto from a single Linux VPS. Same code that powers `juntoapp.tech` — no feature gating for self-hosters: tier checks return Pro by default when you control the database, and the only paid surface is the mock-checkout UI which you can leave alone or remove.

### Prerequisites

- A VPS with at least **2 vCPU / 4 GB RAM / 20 GB disk** (3 vCPU / 6 GB recommended; voice + transcoding compete for cores).
- Ubuntu 22.04 / 24.04 LTS, root or `sudo` access.
- A domain you control with at least three A-records pointing at the VPS:
  - `api.example.tld`
  - `app.example.tld`
  - `livekit.example.tld`
- Outbound 80/443 reachable (Let's Encrypt + container registry).
- Inbound 80, 443, 7881 (TCP), 7882 (UDP) reachable (LiveKit RTC).

Optional but recommended:

- A separate apex / `www` for the landing page (we host ours on GitHub Pages).
- A Cloudflare account if you need to bypass regional blocks of TMDb image CDN — we ship a Worker for that under `deploy/cf-worker-tmdb-images/`.

### One-time bootstrap

SSH onto the VPS as a sudo user (`ubuntu` works fine):

```bash
# 1. Pull the repo onto the box
git clone https://github.com/AbobaTeam-Production/junto-backend
cd junto-backend

# 2. Stage the deploy tree under /srv/junto/
sudo mkdir -p /srv/junto && sudo chown $USER:$USER /srv/junto
cp -r deploy/vps/* /srv/junto/
cp -r deploy/vps/.env.example /srv/junto/.env  # rename, fill in below
chmod +x /srv/junto/init-server.sh /srv/junto/deploy.sh
cd /srv/junto

# 3. Edit nginx vhosts to your domain — replace every juntoapp.tech
sed -i 's/juntoapp\.tech/your-domain.tld/g' nginx/conf.d/*.conf

# 4. Fill secrets in /srv/junto/.env (see "Required env vars" below)
nano .env
```

Then:

```bash
./init-server.sh
```

This script is idempotent and does:

1. `apt install` docker engine, docker-compose-plugin, certbot, ufw
2. Open inbound 22 / 80 / 443 / 7881 (tcp) / 7882 (udp) in ufw
3. Create `/srv/junto/{firebase,web-incoming}/`
4. Run `certbot certonly --standalone` for `api.example.tld`, `app.example.tld`, `livekit.example.tld` (port 80 must be free, no nginx running yet)
5. Print the next-step checklist

### Required env vars (`/srv/junto/.env`)

Generated values you create yourself:

| Var | How to generate |
|---|---|
| `SECRET_KEY` | `python -c 'import secrets;print(secrets.token_urlsafe(64))'` |
| `POSTGRES_PASSWORD` | `openssl rand -base64 24` |
| `LIVEKIT_API_KEY` | `openssl rand -hex 16` |
| `LIVEKIT_API_SECRET` | `openssl rand -hex 32` |

Domain-shaped values:

| Var | Value |
|---|---|
| `ALLOWED_HOSTS` | `api.example.tld,livekit.example.tld` |
| `CSRF_TRUSTED_ORIGINS` | `https://app.example.tld` |
| `CORS_ALLOWED_ORIGINS` | `https://app.example.tld` |
| `LIVEKIT_WS_URL` | `wss://livekit.example.tld` |

Image registry:

| Var | Value |
|---|---|
| `BACKEND_IMAGE` | `ghcr.io/abobateam-production/junto-backend:main` (or pin a tag) |

Optional integrations:

- `FIREBASE_CREDENTIALS_PATH=/firebase/junto-fcm.json` — drop your Firebase service-account JSON to `/srv/junto/firebase/junto-fcm.json` if you want push. Empty value disables push.
- `TMDB_PROXY_BASE` / `TMDB_IMAGE_BASE` — pointing to your own CF Workers if your region blocks TMDb's CDN. The Worker source is in `deploy/cf-worker-tmdb*/` — `npx wrangler deploy` from each folder.

### Deploy

```bash
# Login to ghcr.io once with a fine-scoped read:packages PAT
echo <PAT> | docker login ghcr.io -u <your-github-user> --password-stdin

cd /srv/junto
./deploy.sh
```

`deploy.sh` runs `docker compose pull → up -d → exec backend python manage.py migrate → seed_plans → collectstatic → nginx -s reload`. Idempotent, safe to re-run.

### Verify

```bash
curl -X POST https://api.example.tld/api/auth/guest/ -H "Content-Type: application/json"
# expect 201 with a JWT
```

Open `https://app.example.tld/` once you've also published the [Flutter Web build](https://github.com/AbobaTeam-Production/junto-frontend) into the `junto_webapp` Docker volume — see the frontend repo's web-deploy workflow for the wiring.

### Updates

```bash
cd /srv/junto
./deploy.sh
```

The compose stack pulls the new image, rolls the backend container, applies new migrations, reloads nginx so it sees the new container IP. No manual cleanup needed.

### Troubleshooting

| Symptom | Likely cause |
|---|---|
| 502 on every request right after deploy | nginx cached a stale upstream IP; `deploy.sh` ends with `nginx -s reload` to fix this — re-run if it failed mid-flight. |
| `seed_plans: Unknown command` | `apps.billing` not installed — make sure you're on a recent image (PR #3 in repo). |
| Voice connects but no audio | LiveKit external IP is wrong. Set `external_ip` in `livekit.yaml` to the VPS public IP and restart the container. |
| `Invalid HTTP_HOST header` | Add the inbound `Host` to `ALLOWED_HOSTS` in `/srv/junto/.env`, restart backend (`docker compose ... up -d backend`), reload nginx. |

### Going further

- **Backups**: `pgdata` is the only stateful volume that matters. `docker compose exec db pg_dump junto > backup.sql` on a cron.
- **Cert renewal**: the `certbot.timer` from apt handles it. Check `systemctl list-timers certbot`.
- **Web build CI**: the frontend repo's `web-deploy.yml` rebuilds and ships the Web app via SSH to `/srv/junto/web-incoming/junto-web.tgz` on every push to main — see [junto-frontend](https://github.com/AbobaTeam-Production/junto-frontend) for the workflow + the deploy-key dance.

## License

AGPL-3.0 — if you run a modified version as a network service you must make the source available to your users.

The frontend (`junto-frontend`) is MIT-licensed; you can rebrand or fork it freely. The combination of MIT frontend + AGPL backend is intentional: keep the user-facing UI permissive, keep the server-side improvements flowing back upstream.
