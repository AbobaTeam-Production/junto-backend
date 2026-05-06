"""Seed plausible WatchSession history for a demo account.

Usage:
    python manage.py seed_fake_stats           # seeds the most recent non-guest user
    python manage.py seed_fake_stats --user sogo
    python manage.py seed_fake_stats --user sogo --sessions 47 --hours 128

The generated rows are spread across the last 90 days and have realistic
durations (mostly 20–110 min, with the occasional short check-in or
3-hour binge). Useful for screenshots and live demos before real users
have accumulated history of their own.
"""

import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.rooms.models import Room
from apps.social.models import WatchSession


class Command(BaseCommand):
    help = 'Generate synthetic WatchSession rows for a profile demo.'

    def add_arguments(self, parser):
        parser.add_argument('--user', type=str, default=None,
                            help='Username to seed (default: most recent non-guest)')
        parser.add_argument('--sessions', type=int, default=47)
        parser.add_argument('--hours', type=int, default=128)
        parser.add_argument('--days', type=int, default=90,
                            help='Spread sessions across the last N days')
        parser.add_argument('--clear', action='store_true',
                            help='Delete existing WatchSessions for this user first')

    def handle(self, *_args, **opts):
        User = get_user_model()
        username = opts['user']
        if username:
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                raise CommandError(f'No user named {username!r}')
        else:
            user = (
                User.objects
                .exclude(username__startswith='Гость_')
                .order_by('-date_joined')
                .first()
            )
            if user is None:
                raise CommandError('No non-guest user found — pass --user explicitly')

        target_sessions = opts['sessions']
        target_seconds = opts['hours'] * 3600
        days = opts['days']

        if opts['clear']:
            removed, _ = WatchSession.objects.filter(user=user).delete()
            self.stdout.write(f'Cleared {removed} existing sessions for {user.username}')

        # Need at least one Room for the FK. If the user already hosts/joined any,
        # cycle through them; otherwise pick any active room; otherwise create
        # one stub on the user's behalf. We don't bother making distinct titles —
        # the profile stats query only cares about counts and durations.
        rooms = list(Room.objects.filter(host=user))
        if not rooms:
            rooms = list(Room.objects.all()[:5])
        if not rooms:
            rooms = [Room.objects.create(host=user)]

        # Build session durations that sum to ~= target_seconds. Each session
        # is sampled from a triangular distribution (5min, 60min, 180min) so
        # most are normal-length but a few short / long ones land in the mix.
        durations = []
        remaining = target_seconds
        for i in range(target_sessions):
            if i == target_sessions - 1:
                d = max(60, remaining)  # last one absorbs the remainder
            else:
                d = int(random.triangular(5 * 60, 180 * 60, 60 * 60))
                d = min(d, max(60, remaining - (target_sessions - i - 1) * 60))
            durations.append(d)
            remaining -= d

        # Random join times across the last `days` days, sorted oldest-first
        # so they read naturally if anyone scrolls a session history later.
        now = timezone.now()
        join_times = sorted(
            now - timedelta(seconds=random.randint(0, days * 86400))
            for _ in range(target_sessions)
        )

        rows = []
        for join_at, duration in zip(join_times, durations):
            rows.append(WatchSession(
                user=user,
                room=random.choice(rooms),
                joined_at=join_at,
                left_at=join_at + timedelta(seconds=duration),
                duration_sec=duration,
            ))

        # `auto_now_add=True` on `joined_at` would override our explicit value
        # at .save() time. Bulk_create skips that signal.
        WatchSession.objects.bulk_create(rows)

        total_h = sum(r.duration_sec for r in rows) / 3600
        self.stdout.write(self.style.SUCCESS(
            f'Seeded {len(rows)} sessions for {user.username} '
            f'(total {total_h:.1f}h across {days}d)'
        ))
