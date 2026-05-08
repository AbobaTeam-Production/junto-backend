from django.core.management.base import BaseCommand

from apps.billing.models import SubscriptionPlan


PLANS = [
    {
        'slug': 'free',
        'title_ru': 'Junto Basic',
        'title_en': 'Junto Basic',
        'subtitle_ru': 'Начните бесплатно',
        'subtitle_en': 'Free forever',
        'price_rub_monthly': 0,
        'price_rub_yearly': 0,
        'price_usd_monthly_cents': 0,
        'price_usd_yearly_cents': 0,
        'features_ru': [
            'До 2 гостей в комнате',
            'Голосовой канал, базовое качество',
            '6 часов совместного просмотра в неделю',
            'История просмотров — 30 дней',
            'Реклама в подборках',
        ],
        'features_en': [
            'Up to 2 guests in a room',
            'Voice channel, standard quality',
            '6 hours of co-watching per week',
            'Watch history — 30 days',
            'Sponsored picks in feed',
        ],
        'max_room_guests': 2,
        'includes_ads': True,
        'max_history_days': 30,
        'position': 0,
    },
    {
        'slug': 'pro',
        'title_ru': 'Junto Pro',
        'title_en': 'Junto Pro',
        'subtitle_ru': 'Для регулярных вечеров',
        'subtitle_en': 'For regular movie nights',
        'price_rub_monthly': 199,
        'price_rub_yearly': 1990,
        'price_usd_monthly_cents': 299,
        'price_usd_yearly_cents': 2999,
        'features_ru': [
            'До 10 гостей в комнате',
            'Без рекламы',
            'Без лимита часов',
            'История просмотров навсегда',
            'Кастомные подборки',
            'Приоритет в торрент-очереди',
            'HD/4K passthrough',
        ],
        'features_en': [
            'Up to 10 guests in a room',
            'Ad-free experience',
            'Unlimited co-watching hours',
            'Watch history kept forever',
            'Custom mood lists',
            'Priority torrent queue',
            'HD/4K passthrough',
        ],
        'max_room_guests': 10,
        'includes_ads': False,
        'max_history_days': None,
        'position': 1,
    },
    {
        'slug': 'cinema',
        'title_ru': 'Junto Cinema',
        'title_en': 'Junto Cinema',
        'subtitle_ru': 'Для большой компании',
        'subtitle_en': 'For the whole crew',
        'price_rub_monthly': 499,
        'price_rub_yearly': 4990,
        'price_usd_monthly_cents': 599,
        'price_usd_yearly_cents': 5999,
        'features_ru': [
            'Всё из Pro',
            'До 25 гостей в комнате',
            'Темы оформления комнат',
            'Watch-party события с пушами друзьям',
            'Студийный bitrate голоса',
            'Эксклюзивные кураторские подборки',
        ],
        'features_en': [
            'Everything in Pro',
            'Up to 25 guests in a room',
            'Custom room themes',
            'Watch-party events with push to friends',
            'Studio-grade voice bitrate',
            'Exclusive curated mood lists',
        ],
        'max_room_guests': 25,
        'includes_ads': False,
        'max_history_days': None,
        'position': 2,
    },
]


class Command(BaseCommand):
    help = 'Seed/refresh subscription plans (free / pro / cinema)'

    def handle(self, *args, **options):
        for entry in PLANS:
            obj, created = SubscriptionPlan.objects.update_or_create(
                slug=entry['slug'],
                defaults={k: v for k, v in entry.items() if k != 'slug'},
            )
            verb = 'created' if created else 'updated'
            self.stdout.write(f'  {verb}: {obj.slug}')
        self.stdout.write(self.style.SUCCESS('seed_plans done'))
