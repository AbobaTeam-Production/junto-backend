"""Find a Rutube trailer for a movie via the public search API.

Endpoint shape (probed 2026-05-08):

    GET https://rutube.ru/api/search/video/?query=<q>&page_size=5

Response:
    {
      "results": [
        {"id": "44b0df3ddc...", "title": "Past Lives", "duration": 204, ...},
        ...
      ]
    }

Embed URL: https://rutube.ru/play/embed/<id>/

We pick the best candidate as: title contains 'трейлер'/'trailer',
duration in 30..300s. If nothing matches the strict filter, the
loosest hit (any video matching the title query) is returned.

If the upstream is unreachable or returns garbage, we return None
silently — the title detail just won't show a play button.
"""

import logging

import httpx

logger = logging.getLogger(__name__)

_BASE = 'https://rutube.ru/api/search/video/'
_TIMEOUT = httpx.Timeout(10.0, connect=4.0)
_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Junto/1.0)',
    'Accept': 'application/json',
}


def _looks_like_trailer(title: str) -> bool:
    t = (title or '').lower()
    return 'трейлер' in t or 'trailer' in t


def _title_matches(video_title: str, expected_titles: list[str]) -> bool:
    """Case-insensitive substring match — the video title must contain
    one of the movie's known titles. Without this Rutube's loose
    matching pulls e.g. "Тамерлан" up for the query "Твое имя
    трейлер".
    """
    vt = (video_title or '').lower()
    for t in expected_titles:
        if t and t.strip() and t.lower().strip() in vt:
            return True
    return False


def find_trailer_id(*, title_ru: str = '', title_orig: str = '',
                    year: int | None = None) -> str | None:
    """Returns a Rutube video id for the movie's trailer, or None.

    Strict-only: the candidate video MUST contain the movie title
    AND a trailer keyword AND have a sane duration (30-300s). No
    loose fallback — a wrong trailer is worse than no trailer.
    """
    titles = [t for t in (title_ru, title_orig) if t and t.strip()]
    if not titles:
        return None

    queries: list[str] = []
    for t in titles:
        if year:
            queries.append(f'{t} трейлер {year}')
        queries.append(f'{t} трейлер')

    for q in queries:
        try:
            resp = httpx.get(
                _BASE,
                params={'query': q, 'page_size': 12},
                headers=_HEADERS,
                timeout=_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.exception('Rutube search failed for %r', q)
            continue

        for r in data.get('results') or []:
            duration = r.get('duration') or 0
            if not (30 <= duration <= 300):
                continue
            video_title = r.get('title', '')
            if not _looks_like_trailer(video_title):
                continue
            if not _title_matches(video_title, titles):
                continue
            video_id = r.get('id')
            if isinstance(video_id, str) and video_id:
                return video_id

    return None
