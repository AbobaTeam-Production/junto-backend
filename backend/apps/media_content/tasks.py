import json as _json
import os
import re
import subprocess
import unicodedata
import urllib.parse
import urllib.request

from celery import shared_task
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.conf import settings


def _get_duration_seconds(input_path: str) -> float | None:
    """Get video duration in seconds via ffprobe."""
    probe = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', input_path],
        capture_output=True, text=True,
    )
    if probe.returncode == 0:
        try:
            return float(probe.stdout.strip())
        except (ValueError, TypeError):
            pass
    return None


def _detect_video_codec(input_path: str) -> str | None:
    """Detect video codec of a file via ffprobe."""
    probe = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
         '-show_entries', 'stream=codec_name',
         '-of', 'default=noprint_wrappers=1:nokey=1', input_path],
        capture_output=True, text=True,
    )
    return probe.stdout.strip() if probe.returncode == 0 else None


def _run_ffmpeg_hls(cmd, media_id, room_id, input_path, progress_cb, pct_start, pct_end):
    """Run an ffmpeg HLS command, parse progress, finalize media on success."""
    import threading

    total_duration = _get_duration_seconds(input_path)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    # Drain stderr in a background thread to prevent deadlock
    stderr_chunks = []
    def _drain_stderr():
        for line in proc.stderr:
            if len(stderr_chunks) < 100:
                stderr_chunks.append(line)
    t = threading.Thread(target=_drain_stderr, daemon=True)
    t.start()

    time_pattern = re.compile(r'^out_time_ms=(\d+)')
    for line in proc.stdout:
        m = time_pattern.match(line.strip())
        if m and total_duration and total_duration > 0:
            current_s = int(m.group(1)) / 1_000_000
            ratio = min(current_s / total_duration, 1.0)
            progress_cb(pct_start + int(ratio * (pct_end - pct_start)))

    proc.wait()
    t.join(timeout=5)
    proc._stderr_text = ''.join(stderr_chunks)
    return proc


def _transcode_with_progress(media_id: str, room_id: str, input_path: str,
                              progress_cb, pct_start: int = 50, pct_end: int = 95):
    """Convert video to HLS. Try remux (copy) first, fallback to nvenc, then libx264."""
    from apps.media_content.models import MediaItem

    media = MediaItem.objects.get(id=media_id)
    total_duration = _get_duration_seconds(input_path)

    output_dir = os.path.join(settings.MEDIA_ROOT, 'hls', media_id)
    os.makedirs(output_dir, exist_ok=True)
    output_playlist = os.path.join(output_dir, 'stream.m3u8')
    seg_pattern = os.path.join(output_dir, 'seg_%03d.m4s')

    hls_base = [
        '-force_key_frames', 'expr:gte(t,n_forced*4)',
        '-hls_time', '4',
        '-hls_playlist_type', 'vod',
        '-hls_segment_type', 'fmp4',
        '-hls_segment_filename', seg_pattern,
        '-progress', 'pipe:1',
        '-y',
        output_playlist,
    ]

    codec = _detect_video_codec(input_path)

    # Strategy: copy if H.264/H.265, else nvenc, else libx264
    strategies = []
    if codec in ('h264', 'hevc'):
        strategies.append(('remux', ['-c:v', 'copy', '-c:a', 'aac']))
    strategies.append(('nvenc', ['-c:v', 'h264_nvenc', '-preset', 'p4', '-c:a', 'aac']))
    strategies.append(('libx264', ['-c:v', 'libx264', '-preset', 'ultrafast', '-c:a', 'aac']))

    proc = None
    for name, codec_args in strategies:
        # Clean up previous failed attempt
        for f in os.listdir(output_dir):
            os.remove(os.path.join(output_dir, f))

        cmd = ['ffmpeg', '-i', input_path] + codec_args + hls_base
        proc = _run_ffmpeg_hls(cmd, media_id, room_id, input_path,
                               progress_cb, pct_start, pct_end)
        if proc.returncode == 0:
            break

    if proc is None or proc.returncode != 0:
        stderr_output = getattr(proc, '_stderr_text', '')[:1000] if proc else 'No encoder worked'
        media.status = 'error'
        media.error_message = stderr_output
        media.save(update_fields=['status', 'error_message'])
        return

    duration_int = int(total_duration) if total_duration else None

    hls_relative = os.path.join('hls', media_id, 'stream.m3u8')
    media.hls_path = hls_relative
    media.status = 'ready'
    media.progress = 100
    media.duration_seconds = duration_int
    media.save(update_fields=['hls_path', 'status', 'progress', 'duration_seconds'])

    progress_cb(100)

    from django.core.cache import cache

    # Save as current media if nothing is playing yet
    cache_key = f'room_media_{room_id}'
    if not cache.get(cache_key):
        cache.set(cache_key, _json.dumps({
            'hls_url': f'/media/{hls_relative}',
            'title': media.title,
            'source_type': media.source_type,
            'media_id': str(media.id),
        }), timeout=86400)

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'room_{room_id}',
        {
            'type': 'media.ready',
            'hls_url': f'/media/{hls_relative}',
            'title': media.title,
            'media_id': str(media.id),
        }
    )


VIDEO_EXTENSIONS = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.m4v', '.wmv', '.flv', '.ts'}


def _find_largest_video(directory: str) -> str | None:
    """Find the largest video file in a directory tree."""
    best_path, best_size = None, 0
    for root, _dirs, files in os.walk(directory):
        for f in files:
            if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS:
                full = os.path.join(root, f)
                size = os.path.getsize(full)
                if size > best_size:
                    best_path, best_size = full, size
    return best_path


class _QBitClient:
    """Minimal qBittorrent Web API client."""

    def __init__(self):
        import http.cookiejar
        self.url = os.environ.get('QBITTORRENT_URL', 'http://host.docker.internal:8080')
        cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        self._logged_in = False

    def _ensure_login(self):
        if self._logged_in:
            return
        user = os.environ.get('QBITTORRENT_USER', 'admin')
        pwd = os.environ.get('QBITTORRENT_PASS', 'adminadmin')
        self._post('auth/login', {'username': user, 'password': pwd})
        self._logged_in = True

    def _post(self, endpoint, data=None):
        url = f'{self.url}/api/v2/{endpoint}'
        encoded = urllib.parse.urlencode(data).encode() if data else None
        return self.opener.open(urllib.request.Request(url, data=encoded), timeout=10)

    def _get(self, endpoint, params=None):
        url = f'{self.url}/api/v2/{endpoint}'
        if params:
            url += '?' + urllib.parse.urlencode(params)
        return self.opener.open(urllib.request.Request(url), timeout=10)

    def add_torrent(self, magnet, save_path, category):
        self._ensure_login()
        self._post('torrents/add', {
            'urls': magnet,
            'savepath': save_path,
            'category': category,
            'sequentialDownload': 'true',
            'firstLastPiecePrio': 'true',
        })

    def get_torrent(self, category):
        self._ensure_login()
        resp = self._get('torrents/info', {'category': category})
        torrents = _json.loads(resp.read())
        return torrents[0] if torrents else None

    def remove_torrent(self, torrent_hash):
        try:
            self._post('torrents/pause', {'hashes': torrent_hash})
            self._post('torrents/delete', {
                'hashes': torrent_hash, 'deleteFiles': 'false',
            })
        except Exception:
            pass


@shared_task(bind=True, time_limit=14400, soft_time_limit=14200)
def download_torrent(self, media_id: str, room_id: str, magnet_link: str):
    """Download torrent via qBittorrent, then transcode to HLS."""
    import time
    import shutil
    from apps.media_content.models import MediaItem

    media = MediaItem.objects.get(id=media_id)
    channel_layer = get_channel_layer()
    last_reported = [0]

    def _progress(pct):
        if pct <= last_reported[0]:
            return
        last_reported[0] = pct
        media.progress = pct
        media.save(update_fields=['progress'])
        async_to_sync(channel_layer.group_send)(
            f'room_{room_id}',
            {'type': 'media.progress', 'progress': pct}
        )

    host_download_dir = os.environ.get(
        'TORRENT_DOWNLOAD_DIR', 'H:/PycharmProjects/junto_backend/media_shared/torrents')
    category = f'junto_{room_id}'
    save_path = os.path.join(host_download_dir, room_id).replace('/', '\\')
    download_dir = os.path.join(settings.MEDIA_ROOT, 'torrents', room_id)

    qbt = _QBitClient()

    try:
        qbt.add_torrent(magnet_link, save_path, category)
    except Exception as e:
        media.status = 'error'
        media.error_message = str(e)[:1000]
        media.save(update_fields=['status', 'error_message'])
        return f'Torrent error: {str(e)[:200]}'

    _progress(1)

    # --- Phase 1: download via qBittorrent (1-50%) ---
    torrent_hash = None

    for _ in range(2400):  # up to 2 hours
        time.sleep(3)
        t = qbt.get_torrent(category)
        if not t:
            continue

        torrent_hash = t.get('hash', '')
        t_progress = t.get('progress', 0)
        t_state = t.get('state', '')
        t_name = t.get('name', '')

        if t_name and media.title == 'Torrent download...':
            media.title = t_name
            media.save(update_fields=['title'])

        if t_state in ('error', 'missingFiles'):
            raise RuntimeError(f'qBittorrent error: state={t_state}')

        _progress(max(1, int(t_progress * 50)))

        if t_progress >= 1.0 or t_state in ('uploading', 'pausedUP', 'stalledUP'):
            _progress(50)
            break

    # Remove from qBittorrent (keep files)
    if torrent_hash:
        qbt.remove_torrent(torrent_hash)

    # --- Phase 2: find video and transcode (50-95%) ---
    video_path = _find_largest_video(download_dir)
    if not video_path:
        media.status = 'error'
        media.error_message = 'No video file found in torrent'
        media.save(update_fields=['status', 'error_message'])
        return 'No video file in torrent'

    if media.title == 'Torrent download...':
        media.title = os.path.splitext(os.path.basename(video_path))[0]
        media.save(update_fields=['title'])

    _transcode_with_progress(media_id, room_id, video_path, _progress)

    # Clean up torrent files
    try:
        shutil.rmtree(download_dir)
    except OSError:
        pass

    return f'Torrent complete: {media.title}'


@shared_task(bind=True)
def transcode_to_hls(self, media_id: str, room_id: str, input_path: str):
    """Transcode a video file to HLS format."""
    # Normalize path to NFC to handle macOS NFD-encoded filenames
    input_path = unicodedata.normalize('NFC', input_path)
    if not os.path.exists(input_path):
        parent = os.path.dirname(input_path)
        if os.path.isdir(parent):
            for fname in os.listdir(parent):
                if unicodedata.normalize('NFC', fname) == os.path.basename(input_path):
                    input_path = os.path.join(parent, fname)
                    break

    _transcode_with_progress(media_id, room_id, input_path, lambda p: None, 0, 100)
    return f'Transcoding complete: {media_id}'


@shared_task(bind=True)
def youtube_download(self, media_id: str, room_id: str, url: str):
    """Download YouTube video via yt-dlp, transcode to HLS, notify room."""
    import yt_dlp
    from apps.media_content.models import MediaItem

    media = MediaItem.objects.get(id=media_id)
    channel_layer = get_channel_layer()

    last_reported = [0]

    def _progress(progress):
        if progress <= last_reported[0]:
            return
        last_reported[0] = progress
        async_to_sync(channel_layer.group_send)(
            f'room_{room_id}',
            {'type': 'media.progress', 'progress': progress}
        )

    download_dir = os.path.join(settings.MEDIA_ROOT, 'uploads', room_id)
    os.makedirs(download_dir, exist_ok=True)

    output_template = os.path.join(download_dir, '%(title).80s.%(ext)s')

    # Download progress: 0-50%, transcoding: 50-95%, ready: 100%
    def _download_hook(d):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate') or 0
            downloaded = d.get('downloaded_bytes', 0)
            if total > 0:
                pct = int((downloaded / total) * 50)  # 0-50%
                _progress(max(1, pct))

    cookies_path = os.path.join(settings.BASE_DIR, 'cookies.txt')
    ydl_opts = {
        'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]/best',
        'outtmpl': output_template,
        'merge_output_format': 'mp4',
        'quiet': True,
        'no_warnings': True,
        'no_check_certificates': True,
        'progress_hooks': [_download_hook],
    }
    if os.path.exists(cookies_path):
        ydl_opts['cookiefile'] = cookies_path

    _progress(1)

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'YouTube video')
            downloaded_file = ydl.prepare_filename(info)
            # yt-dlp might merge to .mp4
            if not os.path.exists(downloaded_file):
                base = os.path.splitext(downloaded_file)[0]
                for ext in ('.mp4', '.mkv', '.webm'):
                    if os.path.exists(base + ext):
                        downloaded_file = base + ext
                        break
    except Exception as e:
        media.status = 'error'
        media.error_message = str(e)[:1000]
        media.save(update_fields=['status', 'error_message'])
        return f'yt-dlp error: {str(e)[:200]}'

    if not os.path.exists(downloaded_file):
        media.status = 'error'
        media.error_message = 'Downloaded file not found'
        media.save(update_fields=['status', 'error_message'])
        return 'Downloaded file not found'

    media.title = title
    media.save(update_fields=['title'])
    _progress(50)

    # Transcode to HLS with progress reporting (50-95%)
    _transcode_with_progress(media_id, room_id, downloaded_file, _progress)

    # Clean up downloaded file
    try:
        os.remove(downloaded_file)
    except OSError:
        pass
