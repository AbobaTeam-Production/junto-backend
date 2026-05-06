"""FCM push-delivery wrapper.

`firebase_admin.initialize_app()` is called from `apps.social.apps.ready()`
when `FIREBASE_CREDENTIALS_PATH` is set. If the SDK isn't initialised,
`send_to_user()` is a quiet no-op so dev environments without Firebase
keep working.

Invalid tokens (UNREGISTERED, INVALID_ARGUMENT) are pruned on send so
rotated/wiped tokens don't accumulate.
"""

import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def _send_each(messages):
    """Wrapper around firebase_admin.messaging.send_each.

    Lazy-imported so projects without `firebase-admin` installed (or
    initialised) don't blow up at import time.
    """
    from firebase_admin import messaging
    return messaging.send_each(messages)


def _build_message(token, title, body, data):
    from firebase_admin import messaging
    return messaging.Message(
        token=token,
        notification=messaging.Notification(title=title, body=body),
        # All values must be strings — FCM data payload schema requirement.
        data={k: str(v) for k, v in (data or {}).items()},
    )


def send_to_user(user, *, title, body, data=None):
    """Send a push to every device registered for this user.

    Returns the number of successful deliveries. Devices whose tokens
    FCM rejects as permanently invalid are deleted in-place so we don't
    hammer the same dead token next time.
    """
    if not getattr(settings, 'FCM_ENABLED', False):
        return 0

    from .models import UserDevice

    devices = list(UserDevice.objects.filter(user=user))
    if not devices:
        return 0

    messages = [_build_message(d.fcm_token, title, body, data) for d in devices]

    try:
        response = _send_each(messages)
    except Exception:
        # Network blip / SDK not ready — drop the push, don't poison the
        # caller's request. Worst case the user opens the app and the
        # invalidate-on-poll path picks the new state up.
        logger.exception('FCM send_each failed for user_id=%s', user.id)
        return 0

    # firebase-admin Python SDK signals "token is permanently bad, stop using
    # it" via these specific exception classes. Anything else (network blip,
    # quota, internal) we leave alone — the device might recover.
    from firebase_admin import exceptions as fb_exc
    from firebase_admin import messaging
    permanent_bad = (
        messaging.UnregisteredError,        # token revoked / app uninstalled
        messaging.SenderIdMismatchError,    # token from another project
        fb_exc.NotFoundError,               # 'Requested entity was not found'
        fb_exc.InvalidArgumentError,        # malformed token
    )

    invalid_ids = []
    success = 0
    for device, resp in zip(devices, response.responses):
        if resp.success:
            success += 1
            continue
        err = resp.exception
        if isinstance(err, permanent_bad):
            invalid_ids.append(device.id)
        else:
            logger.warning('FCM send to device=%s failed: %s', device.id, err)

    if invalid_ids:
        UserDevice.objects.filter(id__in=invalid_ids).delete()

    return success
