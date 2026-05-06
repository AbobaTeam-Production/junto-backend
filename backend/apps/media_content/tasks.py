import json as _json
import os
import re
import subprocess
import unicodedata

import httpx
from celery import shared_task
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from django.conf import settings


# Bumped when the cached payload structure or write semantics change. Old
# unversioned entries (`room_media_<id>`) and prior versions are simply left
# to expire — clients re-fetch via /api/rooms/<id>/ on reconnect.
_ROOM_MEDIA_CACHE_VERSION = 'v2'
# 30 min: cache is an optimization for reconnects, not the source of truth.
ROOM_MEDIA_CACHE_TTL = 1800


def room_media_cache_key(room_id):
    return f'room_media_{_ROOM_MEDIA_CACHE_VERSION}_{room_id}'


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


def _run_ffmpeg_hls(cmd, media_id, room_id, input_path, progress_cb, pct_start, pct_end,
                    on_first_segments=None, output_dir=None,
                    min_segments_for_ready=2):
    """Run ffmpeg HLS, stream progress, fire on_first_segments once enough
    HLS segments are on disk for the client to start playing."""
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

    fired = [False]
    def _maybe_fire_ready():
        if fired[0] or on_first_segments is None or output_dir is None:
            return
        try:
            seg_count = sum(
                1 for f in os.listdir(output_dir)
                if f.startswith('seg_') and (f.endswith('.ts') or f.endswith('.m4s'))
            )
            if seg_count >= min_segments_for_ready:
                fired[0] = True
                on_first_segments()
        except OSError:
            pass

    time_pattern = re.compile(r'^out_time_ms=(\d+)')
    for line in proc.stdout:
        m = time_pattern.match(line.strip())
        if m and total_duration and total_duration > 0:
            current_s = int(m.group(1)) / 1_000_000
            ratio = min(current_s / total_duration, 1.0)
            progress_cb(pct_start + int(ratio * (pct_end - pct_start)))
        _maybe_fire_ready()

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
    seg_pattern = os.path.join(output_dir, 'seg_%03d.ts')

    # MPEG-TS segments instead of fmp4: ffmpeg's fmp4 generator was producing
    # segments with a track-id that didn't match init.mp4 (`trun track id
    # unknown, no tfhd was found`), which made Chromium MSE reject the
    # stream with CHUNK_DEMUXER_ERROR_APPEND_FAILED. TS doesn't carry that
    # state across init+segment, so it's unaffected.
    #
    # event playlist lets the client start playing as soon as the first
    # segments are written. ffmpeg writes #EXT-X-ENDLIST automatically on
    # graceful exit, finalizing the playlist as VOD.
    hls_base = [
        # Force a keyframe exactly every 4 seconds independent of source
        # framerate or scene-cut timing. -g/-keyint_min alone aren't enough
        # for variable-fps sources, and a short opening shot without an
        # IDR can leave seg_000.ts without proper SPS/PPS, which Chrome's
        # MSE rejects with DEMUXER_ERROR_COULD_NOT_PARSE.
        '-force_key_frames', 'expr:gte(t,n_forced*4)',
        '-g', '96',
        '-keyint_min', '96',
        '-sc_threshold', '0',
        '-hls_time', '4',
        '-hls_playlist_type', 'event',
        '-hls_flags', 'independent_segments+temp_file',
        '-hls_segment_filename', seg_pattern,
        '-progress', 'pipe:1',
        '-y',
        output_playlist,
    ]

    # Always re-encode for HLS, even from H.264 sources. -c:v copy seems
    # like a free win, but ffmpeg can't synthesize keyframes during copy,
    # so HLS segments come out at the source's native GOP size (often 10s)
    # plus odd-length remainders. Chromium's MSE then trips with
    # CHUNK_DEMUXER_ERROR_APPEND_FAILED when a short segment shows up.
    # Re-encoding with `-preset ultrafast` keeps CPU cost low and gives us
    # uniform 4s segments. NVENC tried first if a GPU is present.
    # Audio: downmix to stereo at 48k/128k. Source files (especially BD/WEB
    # rips) frequently carry 5.1 AAC with `channel_layout=unknown`. Chrome's
    # MSE refuses those segments with CHUNK_DEMUXER_ERROR_APPEND_FAILED, so
    # the <video> never even attaches to the DOM. -ac 2 sidesteps that
    # entirely and trims a bit of bandwidth too.
    #
    # Video: pin pix_fmt=yuv420p and force even dimensions. libx264's
    # baseline H.264 already does this, but nvenc and odd-width sources
    # (e.g. 1194x500) can drift outside what MSE accepts.
    common_video = ['-pix_fmt', 'yuv420p',
                    '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2']
    common_audio = ['-c:a', 'aac', '-ac', '2', '-ar', '48000', '-b:a', '128k']
    strategies = [
        ('nvenc', ['-c:v', 'h264_nvenc', '-preset', 'p4', '-profile:v', 'main']
                  + common_video + common_audio),
        ('libx264', ['-c:v', 'libx264', '-preset', 'ultrafast', '-profile:v', 'main']
                    + common_video + common_audio),
    ]

    # As soon as ffmpeg has written the playlist + a few segments, mark
    # media ready and broadcast — web clients can start streaming HLS
    # while encoding continues in the background.
    hls_relative = os.path.join('hls', media_id, 'stream.m3u8')

    def _on_first_segments():
        from django.core.cache import cache
        print(f'[transcode] early-ready fires for media_id={media_id}', flush=True)
        try:
            MediaItem.objects.filter(id=media_id).update(
                hls_path=hls_relative, status='ready'
            )
        except Exception as e:
            print(f'[transcode] early-ready DB update failed: {e}', flush=True)
            return
        hls_url = f'/media/{hls_relative}'
        raw_url = media.raw_stream_url or ''
        cache.set(room_media_cache_key(room_id), _json.dumps({
            'hls_url': hls_url,
            'raw_stream_url': raw_url,
            'title': media.title,
            'source_type': media.source_type,
            'media_id': str(media.id),
        }), timeout=ROOM_MEDIA_CACHE_TTL)
        async_to_sync(get_channel_layer().group_send)(
            f'room_{room_id}',
            {
                'type': 'media.ready',
                'hls_url': hls_url,
                'raw_stream_url': raw_url,
                'title': media.title,
                'media_id': str(media.id),
                'source_type': media.source_type,
            },
        )

    proc = None
    for name, codec_args in strategies:
        # Clean up previous failed attempt
        for f in os.listdir(output_dir):
            os.remove(os.path.join(output_dir, f))

        cmd = ['ffmpeg', '-i', input_path] + codec_args + hls_base
        proc = _run_ffmpeg_hls(
            cmd, media_id, room_id, input_path,
            progress_cb, pct_start, pct_end,
            on_first_segments=_on_first_segments,
            output_dir=output_dir,
            # Wait for 3 complete segments before signalling ready.
            # Counting only segments that exist when seg_(N+1) is being
            # written guarantees seg_0..N are flushed and parseable;
            # firing on a single in-flight segment causes Chrome MSE to
            # trip DEMUXER_ERROR_COULD_NOT_PARSE on partial PMT/SPS.
            min_segments_for_ready=3,
        )
        if proc.returncode == 0:
            break

    if proc is None or proc.returncode != 0:
        stderr_output = getattr(proc, '_stderr_text', '')[:1000] if proc else 'No encoder worked'
        media.status = 'error'
        media.error_message = stderr_output
        media.save(update_fields=['status', 'error_message'])
        from django.core.cache import cache
        cache.delete(f'transcode_lock_{media_id}')
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

    hls_url = f'/media/{hls_relative}'
    raw_url = media.raw_stream_url or ''

    # Refresh current-media cache with both URLs so a reconnecting client
    # gets the right pair on state_sync.
    cache_key = room_media_cache_key(room_id)
    cache.set(cache_key, _json.dumps({
        'hls_url': hls_url,
        'raw_stream_url': raw_url,
        'title': media.title,
        'source_type': media.source_type,
        'media_id': str(media.id),
    }), timeout=ROOM_MEDIA_CACHE_TTL)

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'room_{room_id}',
        {
            'type': 'media.ready',
            'hls_url': hls_url,
            'raw_stream_url': raw_url,
            'title': media.title,
            'media_id': str(media.id),
            'source_type': media.source_type,
        }
    )

    # Release the request-transcode lock so future re-runs (e.g. after
    # rebuild) can spawn cleanly.
    cache.delete(f'transcode_lock_{media_id}')


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


class _TorrServerClient:
    """Minimal TorrServer MatriX REST API client.

    Two URLs:
      * `url` — internal (backend ↔ torrserver), used for admin API calls.
      * `public_url` — what we hand back to clients in stream URLs. Must be
        reachable from the browser (typically a reverse-proxied path on the
        same origin as the frontend).
    """

    def __init__(self):
        url = os.environ.get('TORRSERVER_URL', '').strip()
        if not url:
            raise RuntimeError(
                'TORRSERVER_URL is not configured. '
                'Set it in environment (e.g. http://torrserver:8090).'
            )
        self.url = url.rstrip('/')
        public = os.environ.get('TORRSERVER_PUBLIC_URL', '').strip()
        # Fall back to the internal URL only if explicitly nothing else is set
        # — useful for non-browser clients (e.g. native apps on the same LAN).
        self.public_url = (public or self.url).rstrip('/')

    def _post_json(self, endpoint, payload):
        resp = httpx.post(
            f'{self.url}/{endpoint}',
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json() if resp.content else {}

    def add_torrent(self, magnet_link, title=''):
        # save_to_db=True keeps the torrent registered across the 30s idle
        # timeout, so a viewer who pauses or seeks doesn't lose the cache.
        return self._post_json('torrents', {
            'action': 'add',
            'link': magnet_link,
            'title': title,
            'save_to_db': True,
        })

    def get_stat(self, torrent_hash):
        """Get torrent status with file list via /stream?stat endpoint."""
        resp = httpx.get(
            f'{self.url}/stream',
            params={'link': torrent_hash, 'stat': '', 'preload': ''},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def get_stream_url(self, torrent_hash, file_id):
        return f'{self.public_url}/play/{torrent_hash}/{file_id}'

    def remove_torrent(self, torrent_hash):
        try:
            self._post_json('torrents', {
                'action': 'rem',
                'hash': torrent_hash,
            })
        except Exception:
            pass


_TORRENT_POLL_INTERVAL = 2
_TORRENT_POLL_MAX_ATTEMPTS = 60  # ~2 minutes total


def _torrent_progress(room_id: str, media, pct: int):
    media.progress = pct
    media.save(update_fields=['progress'])
    async_to_sync(get_channel_layer().group_send)(
        f'room_{room_id}',
        {'type': 'media.progress', 'progress': pct},
    )


def _torrent_fail(media, message: str, ts: _TorrServerClient | None, torrent_hash: str | None):
    media.status = 'error'
    media.error_message = message
    media.save(update_fields=['status', 'error_message'])
    if ts and torrent_hash:
        ts.remove_torrent(torrent_hash)
    return message


def _torrent_finalize(media, room_id: str, ts: _TorrServerClient,
                     torrent_hash: str, file_stats: list):
    """Pick largest video file, broadcast a hybrid media.ready event.

    Hybrid strategy:
      * Native clients (Android, desktop with libmpv) get the raw torrserver
        URL — they can play any container/codec instantly with zero server
        load.
      * Browser clients (Web) need an HLS transcode because <video> only
        decodes a narrow set of formats. We queue transcode_to_hls in the
        background; when it finishes (or first segments are written) it
        broadcasts a second media.ready with the HLS URL.
    """
    best_file = None
    best_size = 0
    for fs in file_stats:
        path = fs.get('path', '')
        ext = os.path.splitext(path)[1].lower()
        if ext in VIDEO_EXTENSIONS:
            size = fs.get('length', 0)
            if size > best_size:
                best_file = fs
                best_size = size

    if not best_file:
        return _torrent_fail(media, 'No video file found in torrent', ts, torrent_hash)

    if media.title == 'Torrent download...':
        media.title = os.path.splitext(os.path.basename(best_file['path']))[0]

    public_stream_url = ts.get_stream_url(torrent_hash, best_file['id'])

    # Mark the media as playable for native clients NOW. HLS will fill in later.
    media.raw_stream_url = public_stream_url
    media.status = 'ready'
    media.progress = 100
    media.save(update_fields=['raw_stream_url', 'status', 'progress', 'title'])

    from django.core.cache import cache
    cache_key = room_media_cache_key(room_id)
    if not cache.get(cache_key):
        cache.set(cache_key, _json.dumps({
            'hls_url': '',
            'raw_stream_url': public_stream_url,
            'title': media.title,
            'source_type': media.source_type,
            'media_id': str(media.id),
        }), timeout=ROOM_MEDIA_CACHE_TTL)

    async_to_sync(get_channel_layer().group_send)(
        f'room_{room_id}',
        {
            'type': 'media.ready',
            'hls_url': '',
            'raw_stream_url': public_stream_url,
            'title': media.title,
            'media_id': str(media.id),
            'source_type': media.source_type,
        },
    )

    # Stash the internal stream URL so /api/media/<id>/request-transcode/
    # can pick it up later when (and only if) a Web client actually needs
    # HLS. Native clients play `raw_stream_url` directly and skip transcode
    # entirely — no point burning CPU for no audience.
    from django.core.cache import cache
    internal_stream_url = f'{ts.url}/play/{torrent_hash}/{best_file["id"]}'
    cache.set(
        f'transcode_input_{media.id}', internal_stream_url, timeout=86400
    )
    return f'Torrent ready (raw): {media.title}; HLS transcode deferred'


@shared_task(bind=True, time_limit=300, soft_time_limit=280,
             max_retries=_TORRENT_POLL_MAX_ATTEMPTS)
def download_torrent(self, media_id: str, room_id: str, magnet_link: str,
                     torrent_hash: str | None = None):
    """Add torrent to TorrServer and poll for metadata via Celery retry.

    Worker is freed between poll attempts (countdown=2s, up to ~2 min total)
    instead of blocking with time.sleep.
    """
    from apps.media_content.models import MediaItem

    media = MediaItem.objects.get(id=media_id)

    try:
        ts = _TorrServerClient()
    except RuntimeError as e:
        return _torrent_fail(media, str(e), None, None)

    # First invocation: add torrent and capture hash
    if torrent_hash is None:
        try:
            result = ts.add_torrent(magnet_link, title=media.title)
        except Exception as e:
            return _torrent_fail(media, f'TorrServer add error: {str(e)[:500]}', None, None)

        torrent_hash = result.get('hash', '')
        if not torrent_hash:
            return _torrent_fail(media, 'TorrServer returned no hash', None, None)
        _torrent_progress(room_id, media, 10)

    # Poll for metadata
    try:
        stat = ts.get_stat(torrent_hash)
    except Exception:
        stat = None

    if stat:
        title = stat.get('title') or stat.get('name', '')
        if title and media.title == 'Torrent download...':
            media.title = title
            media.save(update_fields=['title'])

        file_stats = stat.get('file_stats') or []
        if file_stats:
            _torrent_progress(room_id, media, 50)
            return _torrent_finalize(media, room_id, ts, torrent_hash, file_stats)

    if self.request.retries >= _TORRENT_POLL_MAX_ATTEMPTS - 1:
        return _torrent_fail(
            media,
            'TorrServer: no files found in torrent (metadata timeout)',
            ts, torrent_hash,
        )

    _torrent_progress(room_id, media, min(10 + self.request.retries, 45))
    raise self.retry(
        countdown=_TORRENT_POLL_INTERVAL,
        kwargs={'torrent_hash': torrent_hash},
    )


@shared_task(bind=True)
def transcode_to_hls(self, media_id: str, room_id: str, input_path: str):
    """Transcode a video file (or HTTP/torrserver stream URL) to HLS."""
    # Local-file branch: handle macOS NFD-encoded filenames.
    if not input_path.startswith(('http://', 'https://')):
        input_path = unicodedata.normalize('NFC', input_path)
        if not os.path.exists(input_path):
            parent = os.path.dirname(input_path)
            if os.path.isdir(parent):
                for fname in os.listdir(parent):
                    if unicodedata.normalize('NFC', fname) == os.path.basename(input_path):
                        input_path = os.path.join(parent, fname)
                        break

    channel_layer = get_channel_layer()
    last_reported = [-1]

    def _progress(pct):
        if pct == last_reported[0]:
            return
        last_reported[0] = pct
        try:
            from apps.media_content.models import MediaItem
            MediaItem.objects.filter(id=media_id).update(progress=pct)
        except Exception:
            pass
        async_to_sync(channel_layer.group_send)(
            f'room_{room_id}',
            {'type': 'media.progress', 'progress': pct},
        )

    _transcode_with_progress(media_id, room_id, input_path, _progress, 0, 100)
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
