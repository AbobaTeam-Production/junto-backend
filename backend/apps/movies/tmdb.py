"""TMDb (themoviedb.org) API client through a Cloudflare Worker.

Why a Worker — api.themoviedb.org is unreachable from RU. We deploy a
transparent reverse-proxy at `https://tmdb-proxy.<account>.workers.dev`
that forwards `/3/...` paths to api.themoviedb.org with a shared
secret on incoming requests. Same pattern lives in twitch_clips at
`deploy/cf-worker-tmdb/src/index.js`.

Endpoint shape (TMDb v3, doc: developers.themoviedb.org/3):

    GET /3/search/movie?query=...&language=ru-RU
    GET /3/movie/top_rated?language=ru-RU&page=1
    GET /3/trending/movie/week?language=ru-RU
    GET /3/movie/{id}?language=ru-RU&append_to_response=videos
    GET /3/genre/movie/list?language=ru-RU

Auth path: caller sets `X-Proxy-Secret: <TMDB_PROXY_SECRET>` header
(unwrapped by the Worker before forwarding) AND `api_key=<TMDB_API_KEY>`
query param (passed through to TMDb).

Posters / backdrops live on `image.tmdb.org/t/p/<size>{path}`. We
hard-code w500 for posters, w300 for previews, w1280 for backdrops.

If either `TMDB_API_KEY` or `TMDB_PROXY_BASE` is empty, every public
function returns an empty result — the recs system continues to work
with whatever is already in the DB.
"""

import logging
import re
from decimal import Decimal

import httpx
from django.conf import settings
from django.utils.text import slugify

from .models import Genre, Movie

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _image_base() -> str:
    """Live read of `settings.TMDB_IMAGE_BASE` so re-deploys of the
    images Worker take effect without restarting Django."""
    return getattr(settings, 'TMDB_IMAGE_BASE', 'https://image.tmdb.org').rstrip('/') + '/t/p'

# In-memory cache of TMDb genre id → russian name. Populated on first
# call to `_genre_name()`. The Django process restarts often enough
# that staleness isn't an issue.
_GENRE_CACHE: dict[int, str] = {}


def _enabled() -> bool:
    return bool(
        getattr(settings, 'TMDB_API_KEY', '')
        and getattr(settings, 'TMDB_PROXY_BASE', '')
    )


def _headers() -> dict:
    return {
        'Accept': 'application/json',
        'X-Proxy-Secret': settings.TMDB_PROXY_SECRET,
    }


def _proxy_url(path: str) -> str:
    base = settings.TMDB_PROXY_BASE.rstrip('/')
    if not path.startswith('/'):
        path = '/' + path
    return base + path


def _request(path: str, params: dict | None = None) -> dict | list | None:
    if not _enabled():
        return None
    p = dict(params or {})
    p.setdefault('api_key', settings.TMDB_API_KEY)
    try:
        resp = httpx.get(
            _proxy_url(path),
            params=p,
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        logger.exception('TMDb GET %s failed', path)
        return None


def _img(path: str | None, size: str) -> str:
    if not path:
        return ''
    return f'{_image_base()}/{size}{path}'


def _to_decimal(v) -> Decimal | None:
    if v in (None, '', 0):
        return None
    try:
        return Decimal(str(round(float(v), 2)))
    except (TypeError, ValueError):
        return None


def _year(release_date: str | None) -> int | None:
    if not release_date or len(release_date) < 4:
        return None
    try:
        return int(release_date[:4])
    except ValueError:
        return None


def _slug_for_genre(name_ru: str) -> str:
    table = {
        'драма': 'drama',
        'комедия': 'comedy',
        'мелодрама': 'romance',
        'триллер': 'thriller',
        'ужасы': 'horror',
        'фантастика': 'sci-fi',
        'фэнтези': 'fantasy',
        'боевик': 'action',
        'приключения': 'adventure',
        'детектив': 'mystery',
        'криминал': 'crime',
        'военный': 'war',
        'история': 'history',
        'биография': 'biography',
        'мультфильм': 'animation',
        'аниме': 'anime',
        'документальный': 'documentary',
        'документальное': 'documentary',
        'семейный': 'family',
        'семейное': 'family',
        'мюзикл': 'musical',
        'музыка': 'music',
        'спорт': 'sport',
        'вестерн': 'western',
        'короткометражка': 'short',
    }
    n = (name_ru or '').strip().lower()
    return table.get(n) or slugify(n) or re.sub(r'[^a-z0-9]+', '-', n)[:60] or 'genre'


def _genre_name(genre_id: int) -> str | None:
    if not _GENRE_CACHE:
        data = _request('/3/genre/movie/list', params={'language': 'ru-RU'})
        if isinstance(data, dict):
            for g in (data.get('genres') or []):
                gid = g.get('id')
                name = (g.get('name') or '').strip()
                if isinstance(gid, int) and name:
                    _GENRE_CACHE[gid] = name
    return _GENRE_CACHE.get(genre_id)


# ─────────────── Public API ──────────────────────────────────────────


def search(query: str, limit: int = 10) -> list[dict]:
    """Search by title.

    TMDb's search returns placeholder stubs (zero votes, no poster)
    near the top of results — usually pre-release announcements or
    duplicate entries. We filter those out so callers (mood-list
    curation in particular) don't latch onto a ghost record.
    """
    if not query.strip():
        return []
    data = _request('/3/search/movie', params={
        'query': query.strip(),
        'language': 'ru-RU',
        'include_adult': 'false',
        'page': 1,
    })
    if not isinstance(data, dict):
        return []
    results = data.get('results') or []
    cleaned = [
        r for r in results
        if r.get('poster_path') and (r.get('vote_count') or 0) >= 30
    ]
    # If filtering wiped everything (rare query, all zero-vote hits),
    # fall back to the original order so the caller still gets a
    # candidate.
    return (cleaned or results)[:limit]


def top_rated(limit: int = 80) -> list[dict]:
    """Returns highly-rated, well-known feature films.

    TMDb's `/movie/top_rated` enforces only ~200 votes — that lets
    obscure indies (e.g. "Forest Hymn for Little Girls") slip to the
    top. We use `/discover/movie` instead with `vote_count.gte=2000`
    so the catalog is stocked with films most users have heard of.
    Walks pages of 20 until the limit fills.
    """
    out: list[dict] = []
    page = 1
    while len(out) < limit and page <= 20:
        data = _request('/3/discover/movie', params={
            'language': 'ru-RU',
            'sort_by': 'vote_average.desc',
            # ≥5000 votes — drops cult-following indies (e.g. Forest
            # Hymn for Little Girls) that ride a 2k-vote niche to the
            # top. 5k is roughly the bar for "people have heard of it".
            'vote_count.gte': 5000,
            'with_runtime.gte': 60,
            'include_adult': 'false',
            'page': page,
        })
        if not isinstance(data, dict):
            break
        chunk = data.get('results') or []
        if not chunk:
            break
        out.extend(chunk)
        page += 1
    return out[:limit]


def trending_week(limit: int = 20) -> list[dict]:
    data = _request('/3/trending/movie/week', params={'language': 'ru-RU'})
    if not isinstance(data, dict):
        return []
    return (data.get('results') or [])[:limit]


def fetch(tmdb_id: int) -> Movie | None:
    """Pulls full /movie/{id} (with runtime + full genres) and upserts."""
    data = _request(f'/3/movie/{tmdb_id}', params={
        'language': 'ru-RU',
        'append_to_response': 'videos',
    })
    if not isinstance(data, dict):
        return None
    return upsert(data)


def prewarm_images(urls: list[str]) -> int:
    """Hits the image proxy once per URL so CF caches the bytes at
    its edge. Without this, the first user to open the feed faces
    30+ concurrent CDN-warming requests and Cloudflare's free-plan
    burst limit drops some — leaving random posters as placeholders.

    Returns the count of successfully warmed URLs.
    """
    warmed = 0
    for u in urls:
        if not u:
            continue
        try:
            resp = httpx.head(u, timeout=_TIMEOUT, follow_redirects=True)
            if resp.status_code == 200:
                warmed += 1
        except Exception:
            # Single-image failures are benign — frontend will
            # retry via cached_network_image's own logic.
            continue
    return warmed


def upsert(data: dict) -> Movie | None:
    """Upserts a Movie row + its genres from a search / top_rated /
    detail payload. Genre ids are mapped via the cached `/genre/movie/list`
    table when only `genre_ids` are present (list endpoints), or from
    the inline `genres` array (detail endpoint)."""
    tmdb_id = data.get('id')
    if not tmdb_id:
        return None

    title_ru = (data.get('title') or '').strip()
    title_orig = (data.get('original_title') or '').strip()

    movie, _ = Movie.objects.update_or_create(
        tmdb_id=tmdb_id,
        defaults={
            'title_ru': title_ru[:255] or title_orig[:255] or '?',
            'title_orig': title_orig[:255],
            'year': _year(data.get('release_date')),
            'poster_url': _img(data.get('poster_path'), 'w500')[:500],
            'poster_preview_url': _img(data.get('poster_path'), 'w300')[:500],
            'backdrop_url': _img(data.get('backdrop_path'), 'w1280')[:500],
            'duration_min': data.get('runtime') or None,
            'synopsis_ru': (data.get('overview') or '').strip(),
            'short_synopsis': (data.get('overview') or '').strip()[:280],
            # TMDb is /10, scale matches kp_rating field semantics.
            'kp_rating': _to_decimal(data.get('vote_average')),
            'imdb_rating': None,
            'is_series': False,
        },
    )

    # Genres — prefer inline `genres: [{id, name}]` (detail), fall
    # back to `genre_ids: [int]` mapped via the cached genre list.
    genre_objs: list[Genre] = []
    inline = data.get('genres') or []
    if inline:
        for g in inline:
            name = (g.get('name') or '').strip()
            if not name:
                continue
            slug = _slug_for_genre(name)
            gobj, _ = Genre.objects.get_or_create(
                slug=slug, defaults={'name_ru': name},
            )
            if gobj.name_ru != name:
                gobj.name_ru = name
                gobj.save(update_fields=['name_ru'])
            genre_objs.append(gobj)
    else:
        for gid in (data.get('genre_ids') or []):
            name = _genre_name(int(gid)) if isinstance(gid, int) else None
            if not name:
                continue
            slug = _slug_for_genre(name)
            gobj, _ = Genre.objects.get_or_create(
                slug=slug, defaults={'name_ru': name},
            )
            if gobj.name_ru != name:
                gobj.name_ru = name
                gobj.save(update_fields=['name_ru'])
            genre_objs.append(gobj)

    if genre_objs:
        movie.genres.set(genre_objs)
    return movie
