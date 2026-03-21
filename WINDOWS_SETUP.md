# Junto — запуск на Windows

## Что уже сделано (готово к переносу)

### Backend (Django)
- **docker-compose.yml** — убран устаревший `version`, healthcheck PostgreSQL переведён с `CMD-SHELL` на `CMD` (кроссплатформенный формат)
- **tasks.py** — захардкоженный Unix-путь `/app/cookies.txt` заменён на `os.path.join(settings.BASE_DIR, 'cookies.txt')`
- **settings.py** — все пути через `pathlib.Path` и `os.path.join`, конфиги читаются из переменных окружения
- **Subprocess-вызовы ffmpeg/ffprobe** — используют list-форму (не `shell=True`), работают на любой ОС
- **Очистка комнат (rooms/tasks.py)** — `shutil.rmtree`, `os.path.join` — кроссплатформенно

### Frontend (Flutter)
- **server_config.dart** — IP и порт теперь настраиваются через `--dart-define` при сборке:
  ```
  flutter run --dart-define=SERVER_HOST=192.168.1.100 --dart-define=SERVER_PORT=8000
  ```
- **Windows build** — CMakeLists.txt уже настроен, `flutter build windows` работает из коробки
- **Все зависимости** (`video_player`, `flutter_webrtc`, `web_socket_channel`, `file_picker`, `dio`) поддерживают Windows

---

## Что нужно сделать на Windows

### 1. Установить Docker Desktop (обязательно)
Бэкенд запускается через Docker. Установить: https://docs.docker.com/desktop/install/windows-install/

После установки:
```powershell
cd C:\путь\к\Junto
docker compose up -d
```
Всё поднимется автоматически: PostgreSQL, Redis, Django (Daphne), Celery, Nginx.

### 2. Установить Flutter SDK
https://docs.flutter.dev/get-started/install/windows

Проверить:
```powershell
flutter doctor
```

### 3. Настроить IP-адрес сервера
Узнать IP Windows-машины:
```powershell
ipconfig
```
Найти `IPv4 Address` (например, `192.168.1.100`).

**Вариант A** — через `--dart-define` при каждом запуске:
```powershell
flutter run --dart-define=SERVER_HOST=192.168.1.100
```

**Вариант B** — изменить дефолт в `lib/core/api/server_config.dart`:
```dart
static const _lanHost = String.fromEnvironment(
  'SERVER_HOST',
  defaultValue: '192.168.1.100',  // <-- ваш IP
);
```

### 4. Разрешить сетевой доступ (для тестирования с телефона)
Добавить правило в Windows Firewall:
```powershell
netsh advfirewall firewall add rule name="Junto Backend" dir=in action=allow protocol=TCP localport=8000
netsh advfirewall firewall add rule name="Junto Nginx" dir=in action=allow protocol=TCP localport=80
```

### 5. (Опционально) Запуск без Docker
Если нужно запускать бэкенд без Docker:

1. Установить PostgreSQL 16: https://www.postgresql.org/download/windows/
2. Установить Redis (через WSL2 или Memurai): https://github.com/nicehash/memurai или `wsl --install` + `sudo apt install redis`
3. Установить ffmpeg: `winget install ffmpeg` или https://ffmpeg.org/download.html — добавить в PATH
4. Установить Node.js: https://nodejs.org/
5. Создать `.env`:
   ```env
   DATABASE_URL=postgresql://junto:junto_secret@localhost:5432/watchparty
   REDIS_URL=redis://localhost:6379/0
   MEDIA_ROOT=C:\Junto\media
   DEBUG=True
   ALLOWED_HOSTS=*
   CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8080
   ```
6. Запустить:
   ```powershell
   cd backend
   pip install -r requirements.txt
   python manage.py migrate
   python manage.py runserver 0.0.0.0:8000
   # В отдельном терминале:
   celery -A config worker -l info -P solo
   ```
   **Важно:** на Windows Celery нужен флаг `-P solo` (prefork pool не поддерживается).

---

## Возможные проблемы

| Проблема | Решение |
|----------|---------|
| `curl_cffi` не устанавливается | `pip install curl_cffi` требует Visual C++ Build Tools. Установить: https://visualstudio.microsoft.com/visual-cpp-build-tools/ |
| ffmpeg не найден | Убедиться, что ffmpeg добавлен в PATH: `ffmpeg -version` |
| WebRTC не работает на Windows Desktop | `flutter_webrtc` поддерживает Windows, но может потребовать Visual Studio с C++ workload |
| Docker медленный на Windows | Включить WSL2 backend в Docker Desktop Settings > General > Use WSL2 |
| Порт 5432 занят | Если PostgreSQL уже установлен локально, изменить порт в docker-compose.yml: `"5433:5432"` |

---

## Структура проекта

```
Junto/
├── backend/               # Django backend (в Docker)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── config/            # Django settings, ASGI, Celery
│   └── apps/
│       ├── users/         # Регистрация, JWT
│       ├── rooms/         # Комнаты, WebSocket
│       └── media_content/ # Загрузка, HLS, Rutube
├── docker-compose.yml
├── nginx/
│   └── nginx.conf
└── .env.example

StudioProjects/Junto/      # Flutter frontend
├── lib/
│   ├── core/
│   │   ├── api/           # server_config, endpoints, dio client
│   │   ├── providers/     # WebSocket, auth, voice chat
│   │   └── theme/
│   └── features/
│       ├── home/          # Список комнат, создание
│       ├── room/          # Экран просмотра, чат, реакции
│       ├── onboarding/
│       └── auth/
├── android/
├── web/
├── windows/               # Windows build config (готов)
└── pubspec.yaml
```
