"""Firebase Cloud Messaging delivery - the only module that talks to
Firebase. Everything above it (services/notifications.py) deals in "send
this title/body/data to these tokens" and never imports firebase_admin.

Firebase is optional at runtime: until FIREBASE_CREDENTIALS_FILE points at
a real service-account JSON, `is_configured()` is False and callers treat
every send as a dry run. firebase_admin is imported lazily (only once a
credentials file exists) so an unconfigured server never pays its import
cost or its Python-3.9 deprecation warnings, and a broken install can't
stop the app from starting.
"""

import logging
import pathlib
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

# A named app rather than firebase_admin's default one, so nothing else in
# the process (a future Firebase use) can collide with this initialization.
_APP_NAME = "retailops-notifications"
# FCM's per-request limit for send_each.
_BATCH_SIZE = 500

_app = None
_app_lock = threading.Lock()


class PushNotConfigured(Exception):
    """Raised by send_to_tokens when there's no usable Firebase credential."""


@dataclass
class SendResult:
    success_count: int = 0
    # Tokens FCM says will never work again (uninstalled app, rotated token,
    # token from another Firebase project) - the caller deletes these.
    invalid_tokens: List[str] = field(default_factory=list)
    # Tokens that got at least one message through.
    delivered_tokens: List[str] = field(default_factory=list)


def _credentials_path() -> Optional[pathlib.Path]:
    raw = get_settings().firebase_credentials_file.strip()
    if not raw:
        return None
    path = pathlib.Path(raw).expanduser()
    return path if path.is_file() else None


def _get_app():
    """The initialized firebase_admin app, or None when unconfigured.

    Re-checks the file on every call until it succeeds once, so dropping the
    JSON into place later only needs the setting (already present) - not a
    code change. A malformed file is logged and treated as unconfigured
    rather than raised, per "never let a notification failure affect other
    features".
    """
    global _app
    if _app is not None:
        return _app
    path = _credentials_path()
    if path is None:
        return None
    with _app_lock:
        if _app is not None:
            return _app
        try:
            import firebase_admin
            from firebase_admin import credentials

            try:
                _app = firebase_admin.get_app(_APP_NAME)
            except ValueError:
                _app = firebase_admin.initialize_app(credentials.Certificate(str(path)), name=_APP_NAME)
        except Exception:
            logger.exception("Push: couldn't initialize Firebase from %s - treating as not configured", path)
            return None
    return _app


def is_configured() -> bool:
    return _get_app() is not None


def _is_invalid_token_error(exc: Exception) -> bool:
    from firebase_admin import exceptions, messaging

    if isinstance(exc, (messaging.UnregisteredError, messaging.SenderIdMismatchError)):
        return True
    # INVALID_ARGUMENT is also what a malformed payload gets, so only treat
    # it as a dead token when FCM says the token itself is the problem -
    # a bug in our own payload must not wipe every device.
    if isinstance(exc, exceptions.InvalidArgumentError):
        return "registration token" in str(exc).lower()
    return False


def with_store(data: Dict[str, str]) -> Dict[str, str]:
    """`data` plus "store": STORE_CODE (KloudC RetailOps M2, contract 5.6; in multi
    mode the current store's code, from its settings overlay), so an app
    signed in to another store can tell the alert isn't for it. Nothing is
    added while STORE_CODE is blank (dev, tests). Every other key unchanged."""
    from app.store.settings_view import store_code

    code = store_code()
    if not code:
        return dict(data)
    out = dict(data)
    out["store"] = code
    return out


def send_to_tokens(
    tokens: List[str],
    title: str,
    body: str,
    data: Dict[str, str],
    thread_id: Optional[str] = None,
) -> SendResult:
    """Sends one notification to each token. Raises PushNotConfigured when
    Firebase isn't set up; any other failure (network, auth) is logged and
    counted as not delivered, never raised.

    `thread_id` groups notifications on the lock screen (iOS thread-id,
    Android tag - a newer one replaces the older) - e.g. one chat
    conversation instead of a stack of separate messages.
    """
    app = _get_app()
    if app is None:
        raise PushNotConfigured()

    from firebase_admin import messaging

    result = SendResult()
    data = with_store(data)
    # FCM data values must all be strings.
    data = {k: str(v) for k, v in data.items() if v is not None}
    for start in range(0, len(tokens), _BATCH_SIZE):
        batch = tokens[start : start + _BATCH_SIZE]
        messages = [
            messaging.Message(
                token=token,
                notification=messaging.Notification(title=title, body=body),
                data=data,
                apns=messaging.APNSConfig(
                    payload=messaging.APNSPayload(aps=messaging.Aps(sound="default", thread_id=thread_id))
                ),
                android=(
                    messaging.AndroidConfig(notification=messaging.AndroidNotification(tag=thread_id))
                    if thread_id
                    else None
                ),
            )
            for token in batch
        ]
        try:
            response = messaging.send_each(messages, app=app)
        except Exception:
            logger.exception("Push: FCM batch send failed (%d tokens)", len(batch))
            continue
        for token, send_response in zip(batch, response.responses):
            if send_response.success:
                result.success_count += 1
                result.delivered_tokens.append(token)
            elif send_response.exception is not None and _is_invalid_token_error(send_response.exception):
                result.invalid_tokens.append(token)
            else:
                logger.warning("Push: send to a device failed: %s", send_response.exception)
    return result
