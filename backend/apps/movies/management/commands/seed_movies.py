"""Seed the movies catalog from poiskkino.dev + curate 4 mood lists.

    docker compose run --rm backend python manage.py seed_movies

Idempotent — every call upserts. Safe to re-run after schema changes.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.movies import tmdb
from apps.movies.models import Movie, MoodList, MoodEntry


# Curator picks per mood. We search by query and take the first hit —
# poiskkino's search ranks by relevance, so the top result is almost
# always what we mean.
CURATED_MOODS = [
    {
        'slug': 'cozy-friday',
        'title': 'Уютная пятница',
        'subtitle': 'Тихие, медленные, тёплые',
        'hue': 75,
        'position': 0,
        'picks': [
            ('Past Lives', 'Тихая первая любовь и взросление'),
            ('Perfect Days', 'Японская меланхолия и ритуалы'),
            ('Lost in Translation', 'Токио, бессонница, два одиночества'),
            ('Aftersun', 'Отец, дочь, Турция, плёнка'),
            ('Paterson', 'Поэзия в утренней рутине водителя автобуса'),
            ('Little Forest', 'Сезоны, еда, тишина'),
            ('Chungking Express', 'Гонконгская ночная мечта'),
            ('Frances Ha', 'Чёрно-белый Нью-Йорк двадцати-семилетки'),
            ('Amelie', 'Парижская сказка для самой себя'),
        ],
    },
    {
        'slug': 'shout',
        'title': 'Чтобы поорать',
        'subtitle': 'Когда хочется адреналина',
        'hue': 30,
        'position': 1,
        'picks': [
            ('Mad Max: Fury Road', 'Хромовый рёв и пустыня'),
            ('John Wick', 'Балет на оружии'),
            ('Sicario', 'Граница и тьма'),
            ('Heat', 'Лос-анджелесский шахмат'),
            ('The Raid', 'Индонезийская мясорубка с уровнями'),
            ('Baby Driver', 'Музыка, руль, безумная погоня'),
            ('Atomic Blonde', 'Берлинская стена и неон'),
            ('Mission: Impossible — Fallout', 'Том Круз против гравитации'),
        ],
    },
    {
        'slug': 'slow-cinema',
        'title': 'Медленное кино',
        'subtitle': 'Сядь, налей, выдохни',
        'hue': 220,
        'position': 2,
        'picks': [
            ('Drive My Car', 'Театр, утрата, дорога'),
            ('The Banshees of Inisherin', 'Друзья, остров, тишина'),
            ('Anatomy of a Fall', 'Снег, суд, английский'),
            ('The Zone of Interest', 'Сад на краю ада'),
            ('Burning', 'Корея, ревность, пустота'),
            ('Roma', 'Чёрно-белый Мехико глазами няни'),
            ('Stalker', 'Зона, проводник, метафора'),
            ('Solaris Tarkovsky', 'Память как космическая станция'),
            ('Stranger Than Paradise', 'Чёрно-белый Кливленд и кузина из Венгрии'),
        ],
    },
    {
        'slug': 'rewatch',
        'title': 'Пересмотр',
        'subtitle': 'То, что хочется ещё раз',
        'hue': 340,
        'position': 3,
        'picks': [
            ('In the Mood for Love', 'Шёлк, дождь, музыка'),
            ('Moonlight', 'Свет на чёрной коже'),
            ('Call Me by Your Name', 'Лето, абрикосы, Италия'),
            ('La La Land', 'Лос-Анджелес и упущенные шансы'),
            ('Eternal Sunshine of the Spotless Mind', 'Память как снегопад'),
            ('Pulp Fiction', 'Танец, чемодан, иголка'),
            ('The Grand Budapest Hotel', 'Розовый отель и пропавшая картина'),
            ('Whiplash', 'Барабан до крови'),
            ('Spirited Away', 'Купальня, поезд, имя'),
        ],
    },
]


class Command(BaseCommand):
    help = 'Pull a starter catalog from poiskkino.dev + curate mood lists.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=80,
                            help='How many top-rated KP movies to fetch.')

    def handle(self, *args, **options):
        if not tmdb._enabled():
            self.stderr.write(
                'TMDb env not set — need TMDB_API_KEY + TMDB_PROXY_BASE '
                '+ TMDB_PROXY_SECRET in .env. See deploy/cf-worker-tmdb/.'
            )
            return

        # ── Step 1 — bulk top-N for the catalog
        # The discover endpoint omits `runtime`, so we hop to /movie/{id}
        # for each result to get full details (runtime + genres). 80
        # extra calls ≈ 2 seconds against TMDb's free tier.
        target = options.get('limit') or 80
        self.stdout.write(f'Pulling top-{target} TMDb movies…')
        seeded = 0
        for raw in tmdb.top_rated(limit=target):
            tmdb_id = raw.get('id')
            m = tmdb.fetch(tmdb_id) if tmdb_id else None
            if m is None:
                m = tmdb.upsert(raw)
            if m is not None:
                seeded += 1
        self.stdout.write(self.style.SUCCESS(f'  → upserted {seeded} from top'))

        # ── Step 2 — curated picks (search-by-name)
        self.stdout.write('Curating mood lists…')
        with transaction.atomic():
            for mood_def in CURATED_MOODS:
                mood, _ = MoodList.objects.update_or_create(
                    slug=mood_def['slug'],
                    defaults={
                        'title': mood_def['title'],
                        'subtitle': mood_def['subtitle'],
                        'hue': mood_def['hue'],
                        'position': mood_def['position'],
                    },
                )
                MoodEntry.objects.filter(mood=mood).delete()
                for position, (query, why) in enumerate(mood_def['picks']):
                    hits = tmdb.search(query, limit=1)
                    if not hits:
                        self.stderr.write(f'  ! no hit for "{query}"')
                        continue
                    movie = tmdb.fetch(hits[0]['id']) or tmdb.upsert(hits[0])
                    if movie is None:
                        continue
                    MoodEntry.objects.create(
                        mood=mood, movie=movie,
                        position=position, why_text=why,
                    )
                self.stdout.write(f'  ✓ {mood.title}: {mood.entries.count()} entries')

        # ── Step 3 — pre-warm the CF image cache ──
        # Hit each poster / preview / backdrop URL once so the
        # Worker caches the bytes at its edge. Without this, the
        # first user pulling the feed triggers 30+ concurrent
        # CDN-warmups and Cloudflare's burst limit drops some,
        # leaving random tiles as placeholders.
        urls: list[str] = []
        for movie in Movie.objects.all().only(
            'poster_url', 'poster_preview_url', 'backdrop_url',
        ):
            urls.extend(
                u for u in (
                    movie.poster_url,
                    movie.poster_preview_url,
                    movie.backdrop_url,
                ) if u
            )
        if urls:
            self.stdout.write(f'Pre-warming {len(urls)} image URLs at CF edge…')
            warmed = tmdb.prewarm_images(urls)
            self.stdout.write(self.style.SUCCESS(f'  → warmed {warmed}/{len(urls)}'))

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. Movies in DB: {Movie.objects.count()}, '
            f'mood lists: {MoodList.objects.count()}.'
        ))
