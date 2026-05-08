#!/usr/bin/env bash
# One-time bootstrap for a fresh Ubuntu 24.04 VPS.
# Run as `ubuntu` user with passwordless sudo. Idempotent — safe to re-run.

set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

DOMAINS=(api.juntoapp.tech app.juntoapp.tech livekit.juntoapp.tech)
EMAIL="alexfake95@gmail.com"

echo "==> apt update + base packages"
sudo -E apt-get update -qq
sudo -E apt-get install -y -qq ca-certificates curl gnupg ufw

echo "==> Docker engine + compose plugin"
if ! command -v docker >/dev/null 2>&1; then
    sudo install -m 0755 -d /etc/apt/keyrings
    # --batch + --yes so gpg doesn't try to read from /dev/tty under nohup
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | sudo gpg --batch --yes --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo -E apt-get update -qq
    sudo -E apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    sudo usermod -aG docker "$USER"
    echo "    note: log out & back in for the docker group to apply"
fi

echo "==> certbot (apt build, talks directly to /etc/letsencrypt)"
sudo -E apt-get install -y -qq certbot

echo "==> firewall (ufw)"
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 7881/tcp     # LiveKit RTC TCP fallback
sudo ufw allow 7882/udp     # LiveKit RTC media
sudo ufw --force enable
sudo ufw status verbose | head -10

echo "==> /srv/junto layout"
sudo mkdir -p /srv/junto
sudo chown "$USER:$USER" /srv/junto
mkdir -p /srv/junto/firebase

echo "==> Let's Encrypt certs (one --standalone run before nginx is up)"
# certbot --standalone needs port 80 free. We do this once, before nginx
# container boots. Subsequent renewals go through the certbot container
# in compose, using --webroot.
for domain in "${DOMAINS[@]}"; do
    if [[ ! -d /etc/letsencrypt/live/$domain ]]; then
        echo "    obtaining $domain ..."
        sudo certbot certonly --standalone --non-interactive --agree-tos \
            --email "$EMAIL" -d "$domain"
    else
        echo "    cert for $domain already exists"
    fi
done

echo ""
echo "==> Done. Next steps:"
echo "    1. cd /srv/junto"
echo "    2. clone or rsync the deploy/vps/ tree here:"
echo "         scp -r deploy/vps/* ubuntu@<VPS>:/srv/junto/"
echo "    3. cp .env.example .env  &&  edit secrets"
echo "    4. drop the FCM service-account JSON to /srv/junto/firebase/junto-fcm.json"
echo "    5. login to ghcr (one-off, with a fine-scoped PAT that has read:packages):"
echo "         echo \$GHCR_PAT | docker login ghcr.io -u capitansogo --password-stdin"
echo "    6. ./deploy.sh"
