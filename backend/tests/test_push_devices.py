"""A shared store phone's push token must only ever deliver to whoever is
signed in on it now (design §8.3)."""

from app.models import DeviceToken
from tests.conftest import client_for, make_user


def _register(client, token, previous=None):
    body = {"token": token, "platform": "ios"}
    if previous is not None:
        body["previous_token"] = previous
    return client.post("/api/notifications/devices", json=body)


def _owners(db, token):
    db.expire_all()
    return [row.user_id for row in db.query(DeviceToken).filter(DeviceToken.token == token)]


def test_login_as_a_then_b_moves_the_token(db, staff_client, staff2_client, staff, staff2):
    assert _register(staff_client, "phone-1").status_code == 200
    assert _owners(db, "phone-1") == [staff.id]
    # Ravi's session silently expired; Sam signs in on the same phone.
    assert _register(staff2_client, "phone-1").status_code == 200
    assert _owners(db, "phone-1") == [staff2.id], "only the current user gets its pushes"


def test_unregister_without_a_session(db, staff_client, anon_client, staff):
    _register(staff_client, "phone-2")
    r = anon_client.post("/api/notifications/unregister", json={"token": "phone-2"})
    assert r.status_code == 200
    assert _owners(db, "phone-2") == []


def test_unregister_unknown_token_is_quiet(anon_client):
    assert anon_client.post("/api/notifications/unregister", json={"token": "never-seen"}).status_code == 200
    assert anon_client.post("/api/notifications/unregister", json={"token": "  "}).status_code == 200


def test_logout_with_expired_session_detaches_phone(db, staff_client, anon_client, staff):
    from app.models import UserSession

    _register(staff_client, "phone-3")
    # The session expires; the app can't use the authenticated DELETE.
    db.query(UserSession).filter(UserSession.token == staff_client.token).delete()
    db.commit()
    assert staff_client.delete("/api/notifications/devices", params={"token": "phone-3"}).status_code == 401
    assert anon_client.post("/api/notifications/unregister", json={"token": "phone-3"}).status_code == 200
    assert _owners(db, "phone-3") == []


def test_token_refresh_removes_previous_token(db, staff_client, staff):
    _register(staff_client, "old-token")
    assert _register(staff_client, "new-token", previous="old-token").status_code == 200
    assert _owners(db, "old-token") == []
    assert _owners(db, "new-token") == [staff.id]


def test_previous_token_equal_to_token_is_kept(db, staff_client, staff):
    _register(staff_client, "same", previous="same")
    assert _owners(db, "same") == [staff.id]


def test_existing_authenticated_unregister_still_works(db, staff_client):
    _register(staff_client, "phone-4")
    assert staff_client.delete("/api/notifications/devices", params={"token": "phone-4"}).status_code == 200
    assert _owners(db, "phone-4") == []


def test_only_signed_in_user_is_a_recipient(db, staff):
    """End to end: after A then B on one phone, the alert recipient query
    only yields B for that token."""
    from app.services.notifications import register_device

    other = make_user(db, "john", features=["daily_checklists"])
    register_device(db, staff, "shared", "ios")
    register_device(db, other, "shared", "ios")
    rows = db.query(DeviceToken).filter(DeviceToken.token == "shared").all()
    assert [r.user_id for r in rows] == [other.id]
    client_for(db, other)  # sanity: user usable


def test_thread_id_groups_on_ios_and_android(monkeypatch):
    """send_to_tokens puts thread_id on the APNs payload and as the Android
    tag, and leaves both unset when there's no thread."""
    from firebase_admin import messaging

    from app.services import push

    captured = []

    class _Resp:
        success = True
        exception = None

    class _Batch:
        def __init__(self, n):
            self.responses = [_Resp() for _ in range(n)]

    def fake_send_each(messages, app=None):
        captured.extend(messages)
        return _Batch(len(messages))

    monkeypatch.setattr(push, "_get_app", lambda: object())
    monkeypatch.setattr(messaging, "send_each", fake_send_each)

    push.send_to_tokens(["t1"], "T", "B", {"route": "chat"}, thread_id="chat-5")
    push.send_to_tokens(["t2"], "T", "B", {"route": "task"})

    grouped, plain = captured
    assert grouped.apns.payload.aps.thread_id == "chat-5"
    assert grouped.android.notification.tag == "chat-5"
    assert plain.apns.payload.aps.thread_id is None
    assert plain.android is None
