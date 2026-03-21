import os
import re
import subprocess
import unicodedata

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


def _transcode_with_progress(media_id: str, room_id: str, input_path: str,
                              progress_cb, pct_start: int = 50, pct_end: int = 95):
    """Run ffmpeg HLS transcode with progress reporting via callback.

    Maps ffmpeg progress linearly from pct_start to pct_end.
    On success sets media to 'ready' (100%) and sends media.ready WS event.
    """
    from apps.media_content.models import MediaItem

    media = MediaItem.objects.get(id=media_id)

    total_duration = _get_duration_seconds(input_path)

    output_dir = os.path.join(settings.MEDIA_ROOT, 'hls', room_id)
    os.makedirs(output_dir, exist_ok=True)
    output_playlist = os.path.join(output_dir, 'stream.m3u8')

    cmd = [
        'ffmpeg', '-i', input_path,
        '-c:v', 'libx264', '-c:a', 'aac',
        '-force_key_frames', 'expr:gte(t,n_forced*4)',
        '-hls_time', '4',
        '-hls_playlist_type', 'vod',
        '-hls_segment_type', 'fmp4',
        '-hls_segment_filename', os.path.join(output_dir, 'seg_%03d.m4s'),
        '-progress', 'pipe:1',
        '-y',
        output_playlist,
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    # Parse ffmpeg -progress output: lines like "out_time_ms=12345678"
    time_pattern = re.compile(r'^out_time_ms=(\d+)')
    for line in proc.stdout:
        m = time_pattern.match(line.strip())
        if m and total_duration and total_duration > 0:
            current_us = int(m.group(1))
            current_s = current_us / 1_000_000
            ratio = min(current_s / total_duration, 1.0)
            pct = pct_start + int(ratio * (pct_end - pct_start))
            progress_cb(pct)

    proc.wait()

    if proc.returncode != 0:
        stderr_output = proc.stderr.read()
        media.status = 'error'
        media.error_message = stderr_output[:1000]
        media.save(update_fields=['status', 'error_message'])
        return

    duration_int = int(total_duration) if total_duration else None

    hls_relative = os.path.join('hls', room_id, 'stream.m3u8')
    media.hls_path = hls_relative
    media.status = 'ready'
    media.progress = 100
    media.duration_seconds = duration_int
    media.save(update_fields=['hls_path', 'status', 'progress', 'duration_seconds'])

    progress_cb(100)

    # Notify room participants
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'room_{room_id}',
        {
            'type': 'media.ready',
            'hls_url': f'/media/{hls_relative}',
            'title': media.title,
        }
    )


@shared_task(bind=True)
def transcode_to_hls(self, media_id: str, room_id: str, input_path: str):
    """Transcode a video file to HLS format using ffmpeg."""
    from apps.media_content.models import MediaItem

    media = MediaItem.objects.get(id=media_id)

    # Normalize path to NFC to handle macOS NFD-encoded filenames
    input_path = unicodedata.normalize('NFC', input_path)
    # If NFC path doesn't exist, try finding the actual file in the directory
    if not os.path.exists(input_path):
        parent = os.path.dirname(input_path)
        if os.path.isdir(parent):
            for fname in os.listdir(parent):
                if unicodedata.normalize('NFC', fname) == os.path.basename(input_path):
                    input_path = os.path.join(parent, fname)
                    break

    output_dir = os.path.join(settings.MEDIA_ROOT, 'hls', room_id)
    os.makedirs(output_dir, exist_ok=True)

    output_playlist = os.path.join(output_dir, 'stream.m3u8')

    cmd = [
        'ffmpeg', '-i', input_path,
        '-c:v', 'libx264', '-c:a', 'aac',
        '-force_key_frames', 'expr:gte(t,n_forced*4)',
        '-hls_time', '4',
        '-hls_playlist_type', 'vod',
        '-hls_segment_type', 'fmp4',
        '-hls_segment_filename', os.path.join(output_dir, 'seg_%03d.m4s'),
        '-y',
        output_playlist,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        media.status = 'error'
        media.error_message = result.stderr[:1000]
        media.save(update_fields=['status', 'error_message'])
        return f'ffmpeg error: {result.stderr[:200]}'

    # Get duration via ffprobe
    probe = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', input_path],
        capture_output=True, text=True,
    )
    duration = None
    if probe.returncode == 0:
        try:
            duration = int(float(probe.stdout.strip()))
        except (ValueError, TypeError):
            pass

    hls_relative = os.path.join('hls', room_id, 'stream.m3u8')
    media.hls_path = hls_relative
    media.status = 'ready'
    media.progress = 100
    media.duration_seconds = duration
    media.save(update_fields=['hls_path', 'status', 'progress', 'duration_seconds'])

    # Notify room participants
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'room_{room_id}',
        {
            'type': 'media.ready',
            'hls_url': f'/media/{hls_relative}',
            'title': media.title,
        }
    )

    return f'Transcoding complete: {media.title}'


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
