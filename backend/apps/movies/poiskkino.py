"""poiskkino.dev API client.

Endpoint shape (probed 2026-05-06):

    GET https://api.poiskkino.dev/v1.4/movie/search?query=<q>&limit=<N>
    GET https://api.poiskkino.dev/v1.4/movie/<id>
    GET https://api.poiskkino.dev/v1.4/movie?limit=<N>&sortField=rating.kp&sortType=-1

Auth: X-API-KEY: <token> header.

Response shape relevant to us (single movie):
    {
      "id": 1346482,
      "name": "Прошлые жизни",          # Russian title
      "alternativeName": "Past Lives",  # original
      "year": 2023,
      "description": "Нора c детства…",  # long synopsis
      "shortDescription": "…",
      "movieLength": 105,
      "isSeries": false,
      "rating": {"kp": 7.382, "imdb": 7.8, ...},
      "genres": [{"name": "мелодрама"}],
      "poster":   {"url": "...", "previewUrl": "..."},
      "backdrop": {"url": "...", "previewUrl": "..."}
    }

If `settings.POISKKINO_API_KEY` is empty, every public function is a
no-op returning empty results — keeps dev envs without the key
running.
"""

import logging
import re
from decimal import Decimal

import httpx
from django.conf import settings
from django.utils.text import slugify

from .models import Genre, Movie

logger = logging.getLogger(__name__)

_BASE = 'https://api.poiskkino.dev/v1.4'
_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _headers():
    return {'X-API-KEY': settings.POISKKINO_API_KEY, 'Accept': 'application/json'}


def _enabled() -> bool:
    return bool(getattr(settings, 'POISKKINO_API_KEY', ''))


def _to_decimal(v):
    if v in (None, '', 0):
        return None
    try:
        return Decimal(str(round(float(v), 2)))
    except (TypeError, ValueError):
        return None


def _slug_for_genre(name_ru: str) -> str:
    """Latin-only slug. `slugify` strips Cyrillic so we transliterate
    the most common Kinopoisk genres explicitly; anything else falls
    back to the kp_id-suffix slug from the caller's Movie row."""
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
        'семейный': 'family',
        'мюзикл': 'musical',
        'спорт': 'sport',
        'вестерн': 'western',
        'короткометражка': 'short',
    }
    n = (name_ru or '').strip().lower()
    return table.get(n) or slugify(n) or re.sub(r'[^a-z0-9]+', '-', n)[:60] or 'genre'


def search(query: str, limit: int = 10) -> list[dict]:
    """Returns the raw `docs` payload — caller usually picks one
    result and passes its `id` to `fetch()` for the full upsert."""
    if not _enabled() or not query.strip():
        return []
    try:
        resp = httpx.get(
            f'{_BASE}/movie/search',
            params={'query': query.strip(), 'limit': limit},
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get('docs', [])
    except Exception:
        logger.exception('poiskkino.search failed for %r', query)
        return []


def top_n(limit: int = 25) -> list[dict]:
    """Pulls top-rated KP movies for seeding. Uses the v1.4 list
    endpoint — same shape as fetch(), just abridged."""
    if not _enabled():
        return []
    try:
        resp = httpx.get(
            f'{_BASE}/movie',
            params={
                'limit': limit,
                'sortField': 'rating.kp',
                'sortType': '-1',
                # Rule out empty-rating noise that floats to the top.
                'rating.kp': '7-10',
                'votes.kp': '50000-9999999',
                'isSeries': 'false',
                # Strip shorts — poiskkino's top-N otherwise pulls in
                # high-rated 6-minute student films.
                'movieLength': '60-360',
            },
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get('docs', [])
    except Exception:
        logger.exception('poiskkino.top_n failed')
        return []


def fetch(kp_id: int) -> Movie | None:
    """Upserts a Movie row + its genres from the upstream record."""
    if not _enabled():
        return None
    try:
        resp = httpx.get(
            f'{_BASE}/movie/{kp_id}',
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        logger.exception('poiskkino.fetch failed for %s', kp_id)
        return None
    return upsert(data)


def upsert(data: dict) -> Movie | None:
    """Upserts whatever `data` shape comes back from search() / fetch()
    / top_n() — search results are abridged but still have id + name +
    year + genres + rating + (sometimes) poster/backdrop, so we save
    what's there and let a later fetch() fill the gaps."""
    kp_id = data.get('id')
    if not kp_id:
        return None
    poster = data.get('poster') or {}
    backdrop = data.get('backdrop') or {}
    rating = data.get('rating') or {}
    movie, _ = Movie.objects.update_or_create(
        kp_id=kp_id,
        defaults={
            'title_ru': (data.get('name') or data.get('alternativeName') or '').strip()[:255],
            'title_orig': (data.get('alternativeName') or data.get('enName') or '').strip()[:255],
            'year': data.get('year'),
            'poster_url': (poster.get('url') or '')[:500],
            'poster_preview_url': (poster.get('previewUrl') or '')[:500],
            'backdrop_url': (backdrop.get('url') or '')[:500],
            'duration_min': data.get('movieLength') or None,
            'synopsis_ru': (data.get('description') or '').strip(),
            'short_synopsis': (data.get('shortDescription') or '').strip(),
            'kp_rating': _to_decimal(rating.get('kp')),
            'imdb_rating': _to_decimal(rating.get('imdb')),
            'is_series': bool(data.get('isSeries')),
        },
    )
    # Genres — mirror via slug, preserve M2M order via clear+set.
    genre_objs = []
    for g in (data.get('genres') or []):
        name = (g.get('name') or '').strip()
        if not name:
            continue
        slug = _slug_for_genre(name)
        gobj, _ = Genre.objects.get_or_create(slug=slug, defaults={'name_ru': name})
        if gobj.name_ru != name:
            gobj.name_ru = name
            gobj.save(update_fields=['name_ru'])
        genre_objs.append(gobj)
    if genre_objs:
        movie.genres.set(genre_objs)
    return movie
