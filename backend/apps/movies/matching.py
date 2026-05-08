"""Genre-overlap match%.

Algorithm (MVP):
    A = recency-weighted genre vector for user_a (last 20 MovieView)
    B = same for user_b
    score = cosine(A, B) on the union of genres

Recency weight = 1 / (1 + days_since). New views worth more.

If either user has < 3 views → return None + insufficient_data flag.
"""

import math
from collections import defaultdict

from django.utils import timezone

from .models import Genre, MovieView


_MIN_VIEWS = 3
_LOOKBACK = 20


def _vector(user_id: int) -> tuple[dict[int, float], int]:
    qs = (
        MovieView.objects
        .filter(user_id=user_id)
        .select_related('movie')
        .prefetch_related('movie__genres')
        .order_by('-watched_at')[:_LOOKBACK]
    )
    now = timezone.now()
    weights: dict[int, float] = defaultdict(float)
    count = 0
    for view in qs:
        days = max(0.0, (now - view.watched_at).total_seconds() / 86400)
        w = 1.0 / (1.0 + days)
        for g in view.movie.genres.all():
            weights[g.id] += w
        count += 1
    return weights, count


def _cosine(a: dict[int, float], b: dict[int, float]) -> float:
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    dot = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def match_percent(user_a_id: int, user_b_id: int) -> tuple[int | None, bool]:
    """Returns (percent, insufficient_data)."""
    va, ca = _vector(user_a_id)
    vb, cb = _vector(user_b_id)
    if ca < _MIN_VIEWS or cb < _MIN_VIEWS:
        return None, True
    pct = int(round(_cosine(va, vb) * 100))
    return max(0, min(100, pct)), False


def shared_genres(user_a_id: int, user_b_id: int) -> tuple[list[Genre], list[Genre]]:
    """Returns (shared, not_shared) — genres present in both users' top
    weights vs ones present only in A's or only in B's. Used as the
    'Любите оба' chip set on the match screen."""
    va, _ = _vector(user_a_id)
    vb, _ = _vector(user_b_id)
    shared_ids = [g for g in va if g in vb]
    other_ids = list({*va, *vb} - set(shared_ids))
    shared = list(Genre.objects.filter(id__in=shared_ids).order_by('name_ru'))
    other = list(Genre.objects.filter(id__in=other_ids).order_by('name_ru'))
    return shared, other
