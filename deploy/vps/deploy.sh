#!/usr/bin/env bash
# Pull the latest backend image and restart the stack.
# Run from /srv/junto on the VPS.

set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .env ]]; then
    echo "ERROR: .env missing. cp .env.example .env and fill in secrets." >&2
    exit 1
fi

echo "==> docker compose pull"
docker compose -f docker-compose.prod.yml --env-file .env pull

echo "==> docker compose up -d"
docker compose -f docker-compose.prod.yml --env-file .env up -d --remove-orphans

echo "==> waiting for backend to come up"
for _ in {1..30}; do
    if docker compose -f docker-compose.prod.yml --env-file .env exec -T backend \
        python -c 'import django' >/dev/null 2>&1; then
        break
    fi
    sleep 2
done

echo "==> migrations"
docker compose -f docker-compose.prod.yml --env-file .env exec -T backend \
    python manage.py migrate --noinput

echo "==> seed billing plans (idempotent)"
docker compose -f docker-compose.prod.yml --env-file .env exec -T backend \
    python manage.py seed_plans || true

echo "==> collectstatic"
docker compose -f docker-compose.prod.yml --env-file .env exec -T backend \
    python manage.py collectstatic --noinput

echo "==> running services:"
docker compose -f docker-compose.prod.yml --env-file .env ps
