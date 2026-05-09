# Junto VPS deploy

Production stack on a single Ubuntu 24.04 box. All services run as Docker containers, fronted by nginx with Let's Encrypt TLS. The backend image is pulled from `ghcr.io/abobateam-production/junto-backend` (built by the [docker workflow](../../.github/workflows/docker.yml) on every push to main and on `v*` tags).

## What runs on the box

| Service | Port | What it serves |
|---|---|---|
| nginx | 80 / 443 | TLS termination + vhost routing |
| backend (daphne) | 8000 (internal) | Django REST + WebSocket |
| celery + celery-beat | — | Async tasks, scheduled jobs |
| db (postgres 16) | 5432 (internal) | App database |
| redis 7 | 6379 (internal) | Channels layer + Celery broker + Django cache |
| livekit | 7880 / 7881 / 7882-udp | Voice/video SFU |
| jacred | 9117 (internal) | Torrent search shim |
| torrserver | 8090 (internal) | HTTP streaming for magnet sources |
| prometheus | 9090 (internal) | Metrics scrape + 30d TSDB |
| grafana | 3000 (internal) | Dashboards / alerts UI |
| node-exporter | 9100 (internal) | Host metrics (CPU/RAM/disk/net) |
| cadvisor | 8080 (internal) | Per-container metrics |

## Subdomain routing

| Host | What it proxies to |
|---|---|
| `api.juntoapp.tech` | `/api/`, `/ws/`, `/admin/`, `/media/`, `/torrserver/` → backend / torrserver |
| `livekit.juntoapp.tech` | LiveKit signaling (`wss://`) |
| `app.juntoapp.tech` | Static Flutter Web build |
| `grafana.juntoapp.tech` | Grafana UI |
| `juntoapp.tech` | GitHub Pages — landing (handled outside this stack) |

DNS A-records for `api`, `app`, `livekit` point at the VPS; apex points at GitHub Pages — see the landing repo.

## First-time bootstrap

Run on a fresh Ubuntu 24.04 VPS as user `ubuntu` with passwordless sudo.

```bash
# 1. copy the deploy/ tree to the box
rsync -avz deploy/vps/ ubuntu@37.228.88.62:/srv/junto/

# 2. on the VPS:
ssh ubuntu@37.228.88.62
cd /srv/junto
chmod +x init-server.sh deploy.sh
./init-server.sh           # docker, certbot, ufw, certs

# 3. fill in secrets
cp .env.example .env
$EDITOR .env

# 4. drop the FCM service-account JSON (or leave the dir empty if push is off)
mkdir -p firebase
# scp local junto-a622c-firebase-adminsdk-...json ubuntu@37.228.88.62:/srv/junto/firebase/junto-fcm.json

# 5. login to ghcr.io (one-off; needs a Personal Access Token with `read:packages`)
#    create at https://github.com/settings/tokens — classic, scopes: read:packages
echo <PAT> | docker login ghcr.io -u capitansogo --password-stdin

# 6. first deploy
./deploy.sh
```

After step 6 the stack should be live:

- `https://api.juntoapp.tech/api/billing/plans/` — JSON
- `wss://livekit.juntoapp.tech/` — WebSocket handshake (use `wscat` to verify)
- `https://app.juntoapp.tech/` — 404 until you publish the Flutter Web build (see below)

## Updating

Push a tag to the frontend / backend repo → CI builds → on the VPS:

```bash
cd /srv/junto
./deploy.sh
```

`./deploy.sh` does pull + up + migrate + collectstatic + seed_plans (idempotent).

## Publishing the Flutter Web build

The `webapp` Docker volume is the document root for `app.juntoapp.tech`. To populate it:

```bash
# locally, on dev machine
cd C:/Users/alexf/StudioProjects/junto_frontend
flutter build web --release \
    --dart-define=SERVER_HOST=api.juntoapp.tech \
    --dart-define=SERVER_PORT=443 \
    --dart-define=SERVER_SCHEME=https
rsync -avz --delete build/web/ ubuntu@37.228.88.62:/tmp/junto-web/

# on the VPS
ssh ubuntu@37.228.88.62
docker run --rm -v junto_webapp:/dst -v /tmp/junto-web:/src alpine \
    sh -c 'rm -rf /dst/* && cp -r /src/. /dst/'
```

Or wire this into a separate CI job that pushes a tarball to the box on push.

## Monitoring (Grafana)

Grafana lives at `https://grafana.juntoapp.tech`. First-time setup:

```bash
# 1. Add A-record:  grafana.juntoapp.tech → <VPS-IP>

# 2. On the VPS — issue the cert (nginx must already be up)
ssh ubuntu@<VPS-IP>
cd /srv/junto
docker compose -f docker-compose.prod.yml --env-file .env exec nginx \
    sh -c 'mkdir -p /var/www/certbot'
docker run --rm \
    -v /etc/letsencrypt:/etc/letsencrypt \
    -v junto_certbot-www:/var/www/certbot \
    certbot/certbot certonly --webroot -w /var/www/certbot \
    -d grafana.juntoapp.tech --email you@example.com --agree-tos --non-interactive

# 3. Set GRAFANA_ADMIN_PASSWORD in .env (default is "admin" → CHANGE IT)
echo 'GRAFANA_ADMIN_PASSWORD=<long-random>' >> .env

# 4. Bring up the new services + reload nginx
./deploy.sh
docker compose -f docker-compose.prod.yml --env-file .env exec nginx nginx -s reload
```

Login at `https://grafana.juntoapp.tech` with `admin` / your password. Prometheus is preconfigured as the default datasource. Recommended dashboards to import (UI → "Import" → enter ID):

- **1860** — Node Exporter Full (host metrics)
- **193**  — Docker / cadvisor

## Cert renewal

The `certbot` service in compose-profile `renew` is meant to be opted in:

```bash
docker compose -f docker-compose.prod.yml --env-file .env --profile renew up -d certbot
```

It loops every 12h, runs `certbot renew --webroot`, signals nginx to reload. Only needs to be started once.

## Logs

```bash
docker compose -f docker-compose.prod.yml --env-file .env logs -f backend
docker compose -f docker-compose.prod.yml --env-file .env logs -f livekit
```

## Files in this directory

```
docker-compose.prod.yml   — the stack
.env.example              — secrets template (copy to .env, fill in)
livekit.yaml              — LiveKit config
init-server.sh            — one-time bootstrap (docker, certbot, ufw, certs)
deploy.sh                 — pull + up + migrate
nginx/nginx.conf          — main nginx config (default-server, includes)
nginx/conf.d/             — vhosts (api / app / livekit)
```
