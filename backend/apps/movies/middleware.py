"""Bump `presence_<user_id>` on every authenticated request.

Without this, the recs feed only marked the caller as 'online' when
they themselves opened the feed — so peers saw stale presence until
the other side happened to refresh recs. Touching the cache key on
every authed request gives a real "active in the last 10 min" signal.

The middleware skips anonymous requests cheaply (no cache write).
"""

from . import presence


class PresenceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Run the view first — DRF's JWT authenticator sets
        # `request.user` during dispatch, AFTER Django middleware fires.
        # By touching it post-response we pick up authed JWT calls.
        response = self.get_response(request)
        user = getattr(request, 'user', None)
        if user is not None and getattr(user, 'is_authenticated', False):
            try:
                presence.mark_seen(user.id)
            except Exception:
                # Cache outage — never poison the request.
                pass
        return response
