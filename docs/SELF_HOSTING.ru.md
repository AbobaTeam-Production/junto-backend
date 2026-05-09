# Self-host гайд по Junto

Поднимаем свой инстанс Junto на одном Linux-сервере. Тот же код, что крутится на `juntoapp.tech`. Никаких ограничений по тарифу: проверки подписки на твоём self-host вернут Pro, а единственный платный экран — мок-чекаут UI, который можно оставить или убрать.

> Английская версия тех же шагов: [README#self-hosting](../README.md#self-hosting).

## Что нужно

| Ресурс | Минимум | Рекомендую |
|---|---|---|
| CPU | 2 vCPU | 3 vCPU |
| RAM | 4 GB | 6 GB |
| Disk | 20 GB | 40 GB |
| OS | Ubuntu 22.04 / 24.04 LTS | то же самое |
| Доступы | `sudo` без пароля | то же |

**Сеть** (всё это должно быть открыто на VPS):

- Outbound 80 / 443 — Let's Encrypt + ghcr.io + apt
- Inbound 22 (SSH), 80 (HTTP redirect + ACME challenge), 443 (HTTPS), 7881/tcp + 7882/udp (LiveKit RTC)

**Домен.** Нужен один домен с тремя A-записями на IP сервера:

- `api.example.tld` — REST + WebSocket
- `app.example.tld` — Web-клиент (Flutter Web)
- `livekit.example.tld` — голосовой SFU

Опционально:

- Apex / `www` под лендинг (мы свой кладём на GitHub Pages — тогда apex просто A-записи на `185.199.108-111.153`).
- Cloudflare-аккаунт, если из твоего региона блокируется CDN TMDb. У нас есть Worker, который проксирует постеры — `deploy/cf-worker-tmdb-images/`.

## Шаг 1. SSH на сервер

```bash
ssh ubuntu@<your-server-ip>
```

## Шаг 2. Развернуть скелет в `/srv/junto/`

```bash
# 1. Качаем репу
git clone https://github.com/AbobaTeam-Production/junto-backend
cd junto-backend

# 2. Стейджим деплой-дерево под /srv/junto
sudo mkdir -p /srv/junto && sudo chown $USER:$USER /srv/junto
cp -r deploy/vps/* /srv/junto/
cp deploy/vps/.env.example /srv/junto/.env
chmod +x /srv/junto/init-server.sh /srv/junto/deploy.sh
cd /srv/junto

# 3. Прописываем свой домен в nginx-конфиги
sed -i 's/juntoapp\.tech/example.tld/g' nginx/conf.d/*.conf
```

После последней команды проверь содержимое `nginx/conf.d/*.conf` — должно быть три vhost-файла, каждый со своим `server_name api.example.tld` / `app.example.tld` / `livekit.example.tld`.

## Шаг 3. Заполнить `/srv/junto/.env`

Открой `nano /srv/junto/.env` и подставь свои значения.

**Сгенерируй сам** (никогда не коммить эти строки никуда):

| Переменная | Команда для генерации |
|---|---|
| `SECRET_KEY` | `python3 -c 'import secrets;print(secrets.token_urlsafe(64))'` |
| `POSTGRES_PASSWORD` | `openssl rand -base64 24` |
| `LIVEKIT_API_KEY` | `openssl rand -hex 16` |
| `LIVEKIT_API_SECRET` | `openssl rand -hex 32` |

**Подставь свой домен:**

```env
ALLOWED_HOSTS=api.example.tld,livekit.example.tld
CSRF_TRUSTED_ORIGINS=https://app.example.tld
CORS_ALLOWED_ORIGINS=https://app.example.tld
LIVEKIT_WS_URL=wss://livekit.example.tld
DEBUG=False
```

**Образ бэкенда** (либо берёшь готовый с ghcr, либо собираешь свой и подставляешь сюда):

```env
BACKEND_IMAGE=ghcr.io/abobateam-production/junto-backend:main
```

**Опционально, FCM (push-нотификации):**

1. В Firebase Console → Project Settings → Service accounts → Generate new private key, скачать JSON.
2. Положить в `/srv/junto/firebase/junto-fcm.json`.
3. В `.env`: `FIREBASE_CREDENTIALS_PATH=/firebase/junto-fcm.json`.

Пустое значение = push отключены, всё остальное работает.

**Опционально, TMDb через свой CF Worker** (если ваш регион блочит CDN TMDb напрямую):

```bash
cd deploy/cf-worker-tmdb-images
npx wrangler deploy
# скопируй URL воркера в .env как TMDB_IMAGE_BASE
```

То же самое для `deploy/cf-worker-tmdb/`. Если регионального блока нет — оставь дефолтные значения, всё пойдёт через `image.tmdb.org` напрямую.

## Шаг 4. Bootstrap системы

```bash
cd /srv/junto
./init-server.sh
```

Скрипт идемпотентный (можно запускать сколько угодно раз). Делает:

1. `apt install` — Docker engine, docker-compose-plugin, certbot, ufw.
2. ufw — открывает 22 / 80 / 443 / 7881 (tcp) / 7882 (udp).
3. Создаёт `/srv/junto/firebase/` и `/srv/junto/web-incoming/`.
4. `certbot certonly --standalone` для трёх доменов (порт 80 должен быть свободен — на этом этапе ещё нет nginx-контейнера).
5. Печатает чек-лист «что дальше».

Если certbot ругается на rate-limit — подожди час и запусти ещё раз.

## Шаг 5. Залогиниться в registry

Если используешь готовый образ с `ghcr.io/abobateam-production/junto-backend:main` — сначала залогинься. Нужен GitHub PAT (Personal Access Token) с правами `read:packages`:

1. <https://github.com/settings/tokens> → Generate new token (classic) → выбираем `read:packages`.
2. Скопировать токен.

```bash
echo <PAT> | docker login ghcr.io -u <твой-github-user> --password-stdin
```

## Шаг 6. Запустить

```bash
cd /srv/junto
./deploy.sh
```

Что делает скрипт:

1. `docker compose pull` — тянет свежий образ.
2. `docker compose up -d` — поднимает весь стек: backend (Daphne), celery, celery-beat, postgres, redis, livekit, jacred, torrserver, nginx.
3. `python manage.py migrate` — применяет миграции.
4. `python manage.py seed_plans` — создаёт три тарифа (free / pro / cinema), идемпотентно.
5. `python manage.py collectstatic` — собирает статику Django-админки.
6. `nginx -s reload` — обновляет резолв upstream-IP бэкенда.

Через минуту-две всё должно быть онлайн.

## Шаг 7. Проверка

```bash
curl -X POST https://api.example.tld/api/auth/guest/ -H 'Content-Type: application/json'
```

Должно вернуть `201` с JSON-ом, в котором `tokens.access`. Это значит: API живой, TLS работает, БД доступна, миграции применены.

```bash
curl https://livekit.example.tld/  # ожидаем "OK" или "404 Not Found"
# любой ответ от LiveKit — значит TLS на этом субдомене работает
```

## Шаг 8 (опционально). Web-клиент

`app.example.tld` сейчас 404, потому что Docker-волюм `junto_webapp` пуст. Чтобы залить туда Flutter Web:

1. Локально (или в CI):
   ```bash
   git clone https://github.com/AbobaTeam-Production/junto-frontend
   cd junto-frontend
   flutter build web --release
   tar -czf /tmp/junto-web.tgz -C build/web .
   ```

2. Скопировать на сервер и подсунуть в волюм:
   ```bash
   scp /tmp/junto-web.tgz ubuntu@example.tld:/tmp/
   ssh ubuntu@example.tld 'sudo docker run --rm \
       -v junto_webapp:/dst -v /tmp/junto-web.tgz:/src.tgz \
       alpine sh -c "rm -rf /dst/* && tar -xzf /src.tgz -C /dst"'
   ```

3. Перезагрузить страницу `https://app.example.tld/` — должен открыться Flutter Web клиент.

Хочешь автодеплой web на каждый push? В `junto-frontend` уже настроен workflow `web-deploy.yml` — посмотри как там устроен SSH-deploy-key и сделай по аналогии для своего сервера.

## Обновления

```bash
cd /srv/junto
./deploy.sh
```

Образ перетягивается, контейнер бэкенда пересоздаётся, новые миграции применяются, nginx перечитывается. Можно гонять сколько угодно раз — никаких ручных шагов.

## Бэкапы

Стейтфул-данные живут в трёх docker-волюмах:

- `pgdata` — Postgres (главное)
- `media` — `/media/` HLS-сегменты + загруженные файлы (можно потерять, ffmpeg перерасчитает)
- `redisdata` — кэши и Celery-броkер (тоже можно потерять)

Минимум:

```bash
# Дамп Postgres в файл
docker compose -f /srv/junto/docker-compose.prod.yml --env-file /srv/junto/.env \
    exec -T db pg_dump -U junto watchparty > /srv/junto/backup-$(date +%F).sql

# В крон каждый день в 4:00
0 4 * * * /usr/bin/bash -lc 'docker compose -f /srv/junto/docker-compose.prod.yml --env-file /srv/junto/.env exec -T db pg_dump -U junto watchparty | gzip > /srv/backups/junto-$(date +\%F).sql.gz'
```

`pgdata` без `pg_dump` тоже можно копировать (`/var/lib/docker/volumes/junto_pgdata/_data`), но только при остановленном контейнере.

## TLS-серты

Дефолтный `certbot.timer` от apt продлевает их сам. Проверить:

```bash
systemctl list-timers certbot
```

Если хочешь продление через docker — в `docker-compose.prod.yml` есть профиль `renew`, активируй командой:

```bash
docker compose -f /srv/junto/docker-compose.prod.yml --env-file /srv/junto/.env --profile renew up -d certbot
```

Этот контейнер каждые 12 часов делает `certbot renew --webroot` и перезагружает nginx.

## Если что-то не работает

| Симптом | Что проверить |
|---|---|
| `502 Bad Gateway` сразу после deploy | nginx закэшировал старый IP бэкенда. `deploy.sh` в конце делает `nginx -s reload`, но если он сломался — `sudo docker exec junto-nginx-1 nginx -s reload` руками. |
| `seed_plans: Unknown command` | apps.billing не доустановился. Образ старый — перетяни (`docker compose pull backend`). |
| Голос подключается, но не слышно | Внешний IP LiveKit неверный. Открой `livekit.yaml`, поставь `external_ip: <публичный IP сервера>`, restart livekit-контейнера. |
| `Invalid HTTP_HOST header` в логах backend | Нужно добавить хост в `ALLOWED_HOSTS` в `.env`, потом `docker compose up -d backend && nginx reload`. |
| Постеры фильмов 403 | TMDb блочит твой регион. Подними CF Worker (см. шаг 3) и пропиши `TMDB_IMAGE_BASE` в `.env`. |
| Push-уведомления не приходят | `FIREBASE_CREDENTIALS_PATH` пуст или JSON битый. Проверь `docker compose logs backend | grep -i firebase`. |

## Лицензии

- **Backend** (`junto-backend`) — **AGPL-3.0**. Если запускаете модифицированную версию как сетевой сервис — обязаны открыть исходники своим пользователям.
- **Frontend** (`junto-frontend`) — **MIT**. Можно ребрендить, форкать, делать что угодно.

Такой сплит — чтобы UI можно было свободно адаптировать под свою аудиторию, а серверные улучшения возвращались upstream.

## Куда дальше

- Issues и feature requests — <https://github.com/AbobaTeam-Production/junto-backend/issues>
- Проект-обзор — <https://github.com/AbobaTeam-Production>
- API-эндпойнты задокументированы в коде через DRF (`backend/apps/*/views.py` + урлы в `backend/apps/*/urls.py`). Swagger-схему можно прикрутить через `drf-spectacular`, мы пока не приоритезируем.
