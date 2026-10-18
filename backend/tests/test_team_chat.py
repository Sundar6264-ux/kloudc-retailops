"""Phase 2: announcements and chat (design §12). Authorization (including
direct-id probes), client_uuid retries, unread counts, mute/mentions, DM
blocks, delete, report/moderation, images, pagination, the change feed,
the announcement recipient snapshot, and who pushes go to."""

import datetime as dt
import io
import uuid

import pytest
from PIL import Image

from app.models import DeviceToken, User
from app.team import announcements_service as ann
from app.team import chat_service as chat
from app.team.chat_models import Channel, ChannelMember, Message
from app.team.models import Team
from app.timeutil import utcnow
from tests.conftest import add_member, client_for, make_user


@pytest.fixture(autouse=True)
def _daytime(monkeypatch):
    """Quiet hours depend on the real clock - pin them off unless a test
    turns them on."""
    monkeypatch.setattr(chat, "in_quiet_hours", lambda: False)
    monkeypatch.setattr(ann, "in_quiet_hours", lambda: False)


@pytest.fixture
def pushes(monkeypatch):
    """Firebase 'configured', every token delivered; records each send."""
    sent = []

    class Result:
        def __init__(self, tokens):
            self.delivered_tokens = list(tokens)
            self.invalid_tokens = []
            self.success_count = len(tokens)

    def fake_send(tokens, title, body, data, thread_id=None):
        sent.append({"tokens": sorted(tokens), "title": title, "body": body, "data": data, "thread_id": thread_id})
        return Result(tokens)

    monkeypatch.setattr(chat.push, "is_configured", lambda: True)
    monkeypatch.setattr(chat.push, "send_to_tokens", fake_send)
    return sent


def chatter(db, name, admin=False):
    return make_user(db, name, is_admin=admin, features=["team_chat"])


def device(db, user, token=None):
    db.add(DeviceToken(user_id=user.id, token=token or f"tok-{user.username}", platform="ios"))
    db.commit()


def kitchen_channel(client, team):
    channels = client.get("/api/team/channels").json()["channels"]
    return next(c for c in channels if c["team_id"] == team.id)


def send(client, channel_id, body="hi", client_uuid=None, **extra):
    return client.post(
        f"/api/team/channels/{channel_id}/messages",
        json={"body": body, "client_uuid": client_uuid or str(uuid.uuid4()), **extra},
    )


def jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), (200, 10, 10)).save(buf, format="JPEG")
    return buf.getvalue()


# --- Channels + membership -------------------------------------------------------


def test_team_channels_follow_team_membership(db, staff_client, staff, team, admin_client):
    # Not in Kitchen yet: only the Everyone channel.
    names = [c["name"] for c in staff_client.get("/api/team/channels").json()["channels"]]
    assert names == ["Everyone"]

    add_member(db, team, staff)
    names = [c["name"] for c in staff_client.get("/api/team/channels").json()["channels"]]
    assert set(names) == {"Everyone", "Kitchen"}

    kitchen = kitchen_channel(staff_client, team)
    assert send(staff_client, kitchen["id"], "cooler door").status_code == 200

    # Removed from Kitchen -> the Kitchen chat is gone for them, 404 by id.
    admin_client.put(f"/api/team/teams/{team.id}/members", json={"user_ids": []})
    assert staff_client.get(f"/api/team/channels/{kitchen['id']}/messages").status_code == 404
    assert send(staff_client, kitchen["id"]).status_code == 404


def test_chat_needs_the_team_chat_feature(db, staff2_client, team, staff2):
    add_member(db, team, staff2)  # staff2 has team_tasks only
    assert staff2_client.get("/api/team/channels").status_code == 403


def test_non_member_cannot_read_or_post_by_direct_id(db, staff_client, staff, team, admin_client):
    other = chatter(db, "john")
    add_member(db, team, other)
    kitchen = kitchen_channel(client_for(db, other), team)
    for probe in (
        staff_client.get(f"/api/team/channels/{kitchen['id']}"),
        staff_client.get(f"/api/team/channels/{kitchen['id']}/messages"),
        staff_client.get(f"/api/team/channels/{kitchen['id']}/members"),
        send(staff_client, kitchen["id"]),
        staff_client.post(f"/api/team/channels/{kitchen['id']}/read", json={"last_read_message_id": 1}),
        staff_client.get("/api/team/channels/99999/messages"),
    ):
        assert probe.status_code == 404
    # An admin can read it for moderation, but not post into it.
    assert admin_client.get(f"/api/team/channels/{kitchen['id']}/messages").status_code == 200
    assert send(admin_client, kitchen["id"]).status_code == 404


def test_archived_team_channel_disappears(db, staff_client, staff, team, admin_client):
    add_member(db, team, staff)
    kitchen_channel(staff_client, team)
    admin_client.patch(f"/api/team/teams/{team.id}", json={"archived": True})
    names = [c["name"] for c in staff_client.get("/api/team/channels").json()["channels"]]
    assert "Kitchen" not in names


# --- Messages -------------------------------------------------------------------------


def test_client_uuid_retry_returns_the_same_message(db, staff_client, staff, team):
    add_member(db, team, staff)
    kitchen = kitchen_channel(staff_client, team)
    first = send(staff_client, kitchen["id"], "restock rice", client_uuid="abc-1").json()
    again = send(staff_client, kitchen["id"], "restock rice", client_uuid="abc-1").json()
    assert again["id"] == first["id"] and again["duplicate"] is True
    assert db.query(Message).count() == 1
    # Another person may use the same uuid string - it's per sender.
    other = chatter(db, "john")
    add_member(db, team, other)
    assert send(client_for(db, other), kitchen["id"], "x", client_uuid="abc-1").json()["duplicate"] is False


def test_message_validation(db, staff_client, staff, team):
    add_member(db, team, staff)
    kitchen = kitchen_channel(staff_client, team)
    assert send(staff_client, kitchen["id"], "   ").status_code == 400
    assert send(staff_client, kitchen["id"], "x" * 4001).status_code == 400
    assert staff_client.post(f"/api/team/channels/{kitchen['id']}/messages", json={"body": "x"}).status_code == 422


def test_pagination_before_and_after(db, staff_client, staff, team):
    add_member(db, team, staff)
    kitchen = kitchen_channel(staff_client, team)
    ids = [send(staff_client, kitchen["id"], f"m{i}").json()["id"] for i in range(7)]
    page = staff_client.get(f"/api/team/channels/{kitchen['id']}/messages?limit=3").json()
    assert [m["id"] for m in page["messages"]] == ids[-3:] and page["has_more"] is True
    older = staff_client.get(f"/api/team/channels/{kitchen['id']}/messages?limit=3&before_id={ids[-3]}").json()
    assert [m["id"] for m in older["messages"]] == ids[1:4]
    newer = staff_client.get(f"/api/team/channels/{kitchen['id']}/messages?after_id={ids[4]}").json()
    assert [m["id"] for m in newer["messages"]] == ids[5:] and newer["has_more"] is False


def test_unread_counts_and_read_marker(db, staff_client, staff, team):
    add_member(db, team, staff)
    other = chatter(db, "john")
    add_member(db, team, other)
    other_client = client_for(db, other)
    kitchen = kitchen_channel(staff_client, team)
    ids = [send(other_client, kitchen["id"], f"m{i}").json()["id"] for i in range(3)]
    assert kitchen_channel(staff_client, team)["unread"] == 3
    # Their own messages never count.
    assert kitchen_channel(other_client, team)["unread"] == 0
    assert staff_client.get("/api/team/bootstrap").json()["badges"]["unread"] == 3

    r = staff_client.post(f"/api/team/channels/{kitchen['id']}/read", json={"last_read_message_id": ids[1]})
    assert r.json() == {"last_read_message_id": ids[1], "unread": 1}
    # Never moves backwards; clamps to the newest message.
    staff_client.post(f"/api/team/channels/{kitchen['id']}/read", json={"last_read_message_id": 0})
    assert kitchen_channel(staff_client, team)["unread"] == 1
    staff_client.post(f"/api/team/channels/{kitchen['id']}/read", json={"last_read_message_id": 10**9})
    assert kitchen_channel(staff_client, team)["unread"] == 0
    assert kitchen_channel(other_client, team)["seen_by"] == 1


def test_own_delete_and_not_others(db, staff_client, staff, team):
    add_member(db, team, staff)
    other = chatter(db, "john")
    add_member(db, team, other)
    other_client = client_for(db, other)
    kitchen = kitchen_channel(staff_client, team)
    mine = send(staff_client, kitchen["id"], "oops").json()
    theirs = send(other_client, kitchen["id"], "hello").json()

    assert staff_client.delete(f"/api/team/messages/{theirs['id']}").status_code == 403
    out = staff_client.delete(f"/api/team/messages/{mine['id']}").json()
    assert out["deleted"] is True and out["body"] is None and out["deleted_by_moderator"] is False
    listed = staff_client.get(f"/api/team/channels/{kitchen['id']}/messages").json()["messages"]
    assert listed[0]["body"] is None  # "Message deleted" in the UI
    # Someone outside the channel can't even see it exists.
    outsider = client_for(db, chatter(db, "zed"))
    assert outsider.delete(f"/api/team/messages/{mine['id']}").status_code == 404


def test_mentions_are_parsed_to_channel_members_only(db, staff_client, staff, team):
    add_member(db, team, staff)
    john = chatter(db, "john")
    add_member(db, team, john)
    chatter(db, "zed")  # not in Kitchen
    kitchen = kitchen_channel(staff_client, team)
    msg = send(staff_client, kitchen["id"], "@John and @zed check the cooler, email me@x.com").json()
    assert msg["mentions"] == [john.id]


def test_photo_message_and_media_access(db, staff_client, staff, team):
    add_member(db, team, staff)
    kitchen = kitchen_channel(staff_client, team)
    media = staff_client.post(
        "/api/team/media", files={"file": ("x.jpg", jpeg_bytes(), "image/jpeg")}
    ).json()
    msg = send(staff_client, kitchen["id"], "", media_token=media["token"]).json()
    assert msg["media"]["token"] == media["token"]

    john = chatter(db, "john")
    add_member(db, team, john)
    assert client_for(db, john).get(media["url"]).status_code == 200
    outsider = client_for(db, chatter(db, "zed"))
    assert outsider.get(media["url"]).status_code == 404
    # A fake image is refused before it can be attached.
    bad = staff_client.post("/api/team/media", files={"file": ("x.jpg", b"not an image", "image/jpeg")})
    assert bad.status_code == 415
    # Someone else's upload can't be attached.
    john_client = client_for(db, john)
    stolen = send(john_client, kitchen["id"], "", media_token=media["token"])
    assert stolen.status_code == 400


# --- DMs + blocks ---------------------------------------------------------------------


def test_dm_get_or_create_is_one_channel_per_pair(db, staff_client, staff):
    john = chatter(db, "john")
    a = staff_client.post("/api/team/channels/dm", json={"user_id": john.id}).json()
    b = client_for(db, john).post("/api/team/channels/dm", json={"user_id": staff.id}).json()
    assert a["id"] == b["id"] and a["kind"] == "dm" and a["name"] == "john" and b["name"] == "ravi"
    # Can't DM yourself, a stranger id, or someone without chat - same answer.
    for uid in (staff.id, 99999, make_user(db, "nochat").id):
        r = staff_client.post("/api/team/channels/dm", json={"user_id": uid})
        assert r.status_code == 403 and r.json()["detail"] == chat.NEUTRAL_BLOCKED


def test_dm_is_private_to_its_two_people(db, staff_client, staff):
    john = chatter(db, "john")
    dm = staff_client.post("/api/team/channels/dm", json={"user_id": john.id}).json()
    send(staff_client, dm["id"], "private")
    zed = client_for(db, chatter(db, "zed"))
    assert zed.get(f"/api/team/channels/{dm['id']}/messages").status_code == 404
    assert dm["id"] not in [c["id"] for c in zed.get("/api/team/channels").json()["channels"]]


def test_block_stops_dms_both_ways(db, staff_client, staff):
    john = chatter(db, "john")
    john_client = client_for(db, john)
    dm = staff_client.post("/api/team/channels/dm", json={"user_id": john.id}).json()
    send(john_client, dm["id"], "hey")

    assert staff_client.post("/api/team/blocks", json={"user_id": john.id}).status_code == 200
    # Neither side can send; neither can start a new DM.
    assert send(john_client, dm["id"], "hello?").status_code == 403
    assert send(staff_client, dm["id"], "bye").status_code == 403
    assert john_client.post("/api/team/channels/dm", json={"user_id": staff.id}).status_code == 403
    assert staff_client.post("/api/team/channels/dm", json={"user_id": john.id}).status_code == 403
    # The blocker's copy is hidden. The blocked person is never told: their
    # copy says blocked=False, just can_send=False (design §4.4).
    assert dm["id"] not in [c["id"] for c in staff_client.get("/api/team/channels").json()["channels"]]
    theirs = next(c for c in john_client.get("/api/team/channels").json()["channels"] if c["id"] == dm["id"])
    assert theirs["blocked"] is False and theirs["can_send"] is False
    mine = staff_client.get(f"/api/team/channels/{dm['id']}").json()
    assert mine["blocked"] is True and mine["can_send"] is False
    # Neither shows up in the other's people list.
    assert john.id not in [p["id"] for p in staff_client.get("/api/team/people").json()["people"]]
    assert staff.id not in [p["id"] for p in john_client.get("/api/team/people").json()["people"]]
    assert staff_client.get("/api/team/blocks").json()["blocks"][0]["user_id"] == john.id
    assert john_client.get("/api/team/blocks").json()["blocks"] == []

    staff_client.delete(f"/api/team/blocks/{john.id}")
    assert send(john_client, dm["id"], "back").status_code == 200
    assert dm["id"] in [c["id"] for c in staff_client.get("/api/team/channels").json()["channels"]]


def test_blocks_apply_to_admins_and_admins_see_they_exist(db, staff_client, staff, admin, admin_client):
    staff_client.post("/api/team/blocks", json={"user_id": admin.id})
    assert admin_client.post("/api/team/channels/dm", json={"user_id": staff.id}).status_code == 403
    listed = admin_client.get("/api/team/blocks?all=true").json()["blocks"]
    assert listed == [
        {
            "blocker_user_id": staff.id,
            "blocker_name": "ravi",
            "blocked_user_id": admin.id,
            "blocked_name": "boss",
            "created_at": listed[0]["created_at"],
        }
    ]
    # A non-admin asking for all still only gets their own.
    assert client_for(db, chatter(db, "john")).get("/api/team/blocks?all=true").json()["blocks"] == []


def test_block_does_not_affect_team_channels(db, staff_client, staff, team):
    john = chatter(db, "john")
    add_member(db, team, staff)
    add_member(db, team, john)
    staff_client.post("/api/team/blocks", json={"user_id": john.id})
    kitchen = kitchen_channel(staff_client, team)
    assert send(client_for(db, john), kitchen["id"], "team msg").status_code == 200


# --- Mute, pushes -----------------------------------------------------------------------


def test_push_recipients_rules(db, staff, team, pushes):
    john, zed, amy = chatter(db, "john"), chatter(db, "zed"), chatter(db, "amy")
    for u in (staff, john, zed, amy):
        add_member(db, team, u)
        device(db, u)
    ravi = client_for(db, staff)
    kitchen = kitchen_channel(ravi, team)
    # zed muted, amy blocked ravi.
    client_for(db, zed).patch(f"/api/team/channels/{kitchen['id']}/me", json={"muted": True})
    client_for(db, amy).post("/api/team/blocks", json={"user_id": staff.id})

    send(ravi, kitchen["id"], "order is here")
    assert pushes[-1]["tokens"] == ["tok-john"]  # never the sender, muted or blocked
    assert pushes[-1]["title"] == "ravi in Kitchen" and pushes[-1]["body"] == "order is here"
    assert pushes[-1]["data"]["route"] == "chat" and pushes[-1]["data"]["channel_id"] == str(kitchen["id"])
    # One lock-screen group per conversation.
    assert pushes[-1]["thread_id"] == f"chat-{kitchen['id']}"

    # A mention reaches the muted person, never across a block.
    send(ravi, kitchen["id"], "@zed @amy look")
    assert "tok-zed" in pushes[-1]["tokens"] and "tok-amy" not in pushes[-1]["tokens"]


def test_push_throttle_one_per_minute_until_read(db, staff, team, pushes):
    john = chatter(db, "john")
    for u in (staff, john):
        add_member(db, team, u)
        device(db, u)
    ravi, john_c = client_for(db, staff), client_for(db, john)
    kitchen = kitchen_channel(ravi, team)
    send(ravi, kitchen["id"], "one")
    send(ravi, kitchen["id"], "two")
    assert len(pushes) == 1  # second one throttled - john hasn't read yet
    last = send(ravi, kitchen["id"], "three").json()
    assert len(pushes) == 1
    john_c.post(f"/api/team/channels/{kitchen['id']}/read", json={"last_read_message_id": last["id"]})
    send(ravi, kitchen["id"], "four")
    assert len(pushes) == 2  # read since the last push -> pushes again
    # And after a minute even unread ones push again.
    row = db.get(ChannelMember, (kitchen["id"], john.id))
    db.refresh(row)
    row.last_pushed_at = utcnow() - dt.timedelta(seconds=61)
    db.commit()
    send(ravi, kitchen["id"], "five")
    assert len(pushes) == 3


def test_hide_previews_and_chat_alert_off(db, staff, team, pushes):
    john, zed = chatter(db, "john"), chatter(db, "zed")
    for u in (staff, john, zed):
        add_member(db, team, u)
        device(db, u)
    john_c, zed_c = client_for(db, john), client_for(db, zed)
    assert john_c.put("/api/team/chat/settings", json={"hide_previews": True}).json() == {"hide_previews": True}
    assert zed_c.put("/api/notifications/prefs", json={"key": "chat_message", "enabled": False}).status_code == 200
    ravi = client_for(db, staff)
    send(ravi, kitchen_channel(ravi, team)["id"], "secret stuff")
    assert pushes == [
        {
            "tokens": ["tok-john"],
            "title": "ravi in Kitchen",
            "body": "New message in Kitchen",
            "data": pushes[0]["data"],
            "thread_id": pushes[0]["thread_id"],
        }
    ]


def test_quiet_hours_send_no_chat_push(db, staff, team, pushes, monkeypatch):
    john = chatter(db, "john")
    for u in (staff, john):
        add_member(db, team, u)
        device(db, u)
    monkeypatch.setattr(chat, "in_quiet_hours", lambda: True)
    ravi = client_for(db, staff)
    send(ravi, kitchen_channel(ravi, team)["id"], "late")
    assert pushes == []


# --- Reports + moderation ------------------------------------------------------------------


def test_report_and_moderation_flow(db, staff_client, staff, team, admin_client, admin, pushes):
    device(db, admin)
    john = chatter(db, "john")
    add_member(db, team, staff)
    add_member(db, team, john)
    john_c = client_for(db, john)
    kitchen = kitchen_channel(staff_client, team)
    bad = send(john_c, kitchen["id"], "something rude").json()

    # Can't report your own; reporting twice returns the same report.
    assert john_c.post(f"/api/team/messages/{bad['id']}/report", json={"reason": "x"}).status_code == 400
    r1 = staff_client.post(f"/api/team/messages/{bad['id']}/report", json={"reason": "rude"}).json()
    r2 = staff_client.post(f"/api/team/messages/{bad['id']}/report", json={"reason": "rude"}).json()
    assert r1 == r2 and r1["status"] == "open"
    assert pushes[-1]["tokens"] == ["tok-boss"] and pushes[-1]["data"]["route"] == "moderation"
    assert "ravi" not in pushes[-1]["body"]  # never names the reporter

    # Staff can't see the queue or act on it.
    assert staff_client.get("/api/team/reports").status_code == 403
    assert staff_client.post(f"/api/team/reports/{r1['id']}/resolve", json={}).status_code == 403

    queue = admin_client.get("/api/team/reports?status=open").json()["reports"]
    assert [q["id"] for q in queue] == [r1["id"]]
    assert queue[0]["message"]["body"] == "something rude" and queue[0]["reporter"]["name"] == "ravi"

    out = admin_client.post(
        f"/api/team/reports/{r1['id']}/resolve", json={"note": "removed", "delete_message": True}
    ).json()
    assert out["status"] == "resolved" and out["resolved_by"]["name"] == "boss"
    # Deleted for everyone, marked as moderation, original kept for admins.
    shown = staff_client.get(f"/api/team/channels/{kitchen['id']}/messages").json()["messages"][0]
    assert shown["body"] is None and shown["deleted_by_moderator"] is True
    assert out["message"]["body"] == "something rude" and out["message"]["delete_reason"] == "removed"
    assert admin_client.get("/api/team/reports?status=open").json()["reports"] == []
    assert admin_client.get("/api/team/reports?status=bogus").status_code == 400


def test_dismiss_and_admin_direct_delete(db, staff_client, staff, team, admin_client):
    john = chatter(db, "john")
    add_member(db, team, staff)
    add_member(db, team, john)
    kitchen = kitchen_channel(staff_client, team)
    msg = send(client_for(db, john), kitchen["id"], "fine really").json()
    rep = staff_client.post(f"/api/team/messages/{msg['id']}/report", json={"reason": "?"}).json()
    out = admin_client.post(f"/api/team/reports/{rep['id']}/dismiss", json={"note": "ok"}).json()
    assert out["status"] == "dismissed" and out["message"]["deleted"] is False
    # Admin moderation delete straight from the chat, with a reason.
    deleted = admin_client.request("DELETE", f"/api/team/messages/{msg['id']}", json={"reason": "spam"}).json()
    assert deleted["deleted_by_moderator"] is True
    assert db.get(Message, msg["id"]).delete_reason == "spam"


def test_reporting_needs_to_see_the_message(db, staff_client, staff, team):
    john = chatter(db, "john")
    add_member(db, team, john)
    msg = send(client_for(db, john), kitchen_channel(client_for(db, john), team)["id"], "x").json()
    assert staff_client.post(f"/api/team/messages/{msg['id']}/report", json={"reason": "x"}).status_code == 404


# --- Change feed -------------------------------------------------------------------------------


def test_change_feed_only_shows_members_their_channels(db, staff_client, staff, team):
    john = chatter(db, "john")
    add_member(db, team, john)
    john_c = client_for(db, john)
    kitchen = kitchen_channel(john_c, team)
    cursor = staff_client.get("/api/team/bootstrap").json()["cursor"]
    msg = send(john_c, kitchen["id"], "kitchen only").json()

    ravi_updates = staff_client.get(f"/api/team/updates?since={cursor}").json()
    assert "message" not in ravi_updates["changes"]
    john_updates = john_c.get(f"/api/team/updates?since={cursor}").json()
    assert {"id": msg["id"], "action": "created"} in john_updates["changes"]["message"]

    john_c.delete(f"/api/team/messages/{msg['id']}")
    tomb = john_c.get(f"/api/team/updates?since={john_updates['cursor']}").json()
    assert tomb["changes"]["message"] == [{"id": msg["id"], "action": "deleted"}]


# --- Announcements ---------------------------------------------------------------------------------


def post_announcement(admin_client, team_ids, **extra):
    body = {"title": "New closing time", "body": "We close at 9 from Monday.", "team_ids": team_ids, **extra}
    return admin_client.post("/api/team/announcements", json=body)


def test_announcement_recipients_are_a_snapshot(db, admin_client, staff, staff2, team, admin):
    add_member(db, team, staff)
    a = post_announcement(admin_client, [team.id], require_ack=True).json()
    assert a["stats"] == {"recipients": 1, "seen": 0, "acked": 0}

    # Joining later doesn't add you; leaving doesn't remove you.
    add_member(db, team, staff2)
    admin_client.put(f"/api/team/teams/{team.id}/members", json={"user_ids": [staff2.id]})
    recipients = admin_client.get(f"/api/team/announcements/{a['id']}/recipients").json()
    assert [r["user_id"] for r in recipients["recipients"]] == [staff.id]
    assert recipients["total"] == 1
    assert recipients["summary"] == "0 of 1 acknowledged"

    s2 = client_for(db, staff2)
    assert s2.get(f"/api/team/announcements/{a['id']}").status_code == 404
    assert s2.get("/api/team/announcements").json()["announcements"] == []
    ravi = client_for(db, staff)
    listed = ravi.get("/api/team/announcements").json()["announcements"]
    assert [x["id"] for x in listed] == [a["id"]] and listed[0]["stats"] is None  # staff don't see counts


def test_announcement_ack_badge_and_seen(db, admin_client, staff, team):
    add_member(db, team, staff)
    ravi = client_for(db, staff)
    a = post_announcement(admin_client, [team.id], require_ack=True).json()
    assert ravi.get("/api/team/bootstrap").json()["badges"]["announcements_unacked"] == 1
    seen = ravi.post(f"/api/team/announcements/{a['id']}/seen").json()
    assert seen["me"]["seen_at"] and seen["me"]["acked_at"] is None
    acked = ravi.post(f"/api/team/announcements/{a['id']}/ack").json()
    assert acked["me"]["acked_at"]
    assert ravi.get("/api/team/bootstrap").json()["badges"]["announcements_unacked"] == 0
    assert admin_client.get(f"/api/team/announcements/{a['id']}/recipients").json()["summary"] == "1 of 1 acknowledged"


def test_announcements_for_everyone_without_a_feature(db, admin_client):
    plain = make_user(db, "newhire")  # no features at all
    a = post_announcement(admin_client, [db.query(Team).filter(Team.is_everyone.is_(True)).one().id]).json()
    c = client_for(db, plain)
    assert [x["id"] for x in c.get("/api/team/announcements").json()["announcements"]] == [a["id"]]


def test_only_admins_post_edit_remind(db, staff_client, staff, team, admin_client):
    add_member(db, team, staff)
    assert post_announcement(staff_client, [team.id]).status_code == 403
    a = post_announcement(admin_client, [team.id]).json()
    assert staff_client.patch(f"/api/team/announcements/{a['id']}", json={"pinned": True}).status_code == 403
    assert staff_client.post(f"/api/team/announcements/{a['id']}/remind").status_code == 403
    assert staff_client.get(f"/api/team/announcements/{a['id']}/recipients").status_code == 403
    assert post_announcement(admin_client, []).status_code == 400
    assert post_announcement(admin_client, [99999]).status_code == 400

    edited = admin_client.patch(f"/api/team/announcements/{a['id']}", json={"pinned": True, "title": "Updated"}).json()
    assert edited["pinned"] is True and edited["title"] == "Updated" and edited["edited_at"]
    admin_client.patch(f"/api/team/announcements/{a['id']}", json={"deleted": True})
    assert staff_client.get(f"/api/team/announcements/{a['id']}").status_code == 404


def test_announcement_pushes_post_and_remind(db, admin_client, admin, staff, staff2, team, pushes):
    add_member(db, team, staff)
    add_member(db, team, staff2)
    for u in (staff, staff2, admin):
        device(db, u)
    a = post_announcement(admin_client, [team.id], require_ack=True).json()
    assert pushes[-1]["tokens"] == ["tok-ravi", "tok-sam"]  # never the author
    assert pushes[-1]["data"] == {"type": "announcement", "route": "announcement", "announcement_id": str(a["id"])}

    client_for(db, staff).post(f"/api/team/announcements/{a['id']}/ack")
    # A later joiner gets no reminder - only snapshot recipients who haven't acked.
    late = chatter(db, "john")
    add_member(db, team, late)
    device(db, late)
    out = admin_client.post(f"/api/team/announcements/{a['id']}/remind").json()
    assert out == {"reminded": 1, "pending_push": False}
    assert pushes[-1]["tokens"] == ["tok-sam"] and pushes[-1]["title"] == "Reminder: New closing time"
    # Each remind is its own push; the second goes too.
    admin_client.post(f"/api/team/announcements/{a['id']}/remind")
    assert len(pushes) == 3


def test_announcement_push_waits_for_quiet_hours_to_end(db, admin_client, staff, team, pushes, monkeypatch):
    add_member(db, team, staff)
    device(db, staff)
    monkeypatch.setattr(ann, "in_quiet_hours", lambda: True)
    a = post_announcement(admin_client, [team.id]).json()
    assert pushes == []
    assert ann.flush_pending_pushes(db) == 0  # still night
    monkeypatch.setattr(ann, "in_quiet_hours", lambda: False)
    assert ann.flush_pending_pushes(db) == 1
    assert pushes[-1]["data"]["announcement_id"] == str(a["id"])
    assert ann.flush_pending_pushes(db) == 0  # sent once


def test_announcement_media_only_for_recipients(db, admin_client, staff, team):
    add_member(db, team, staff)
    media = admin_client.post("/api/team/media", files={"file": ("a.png", jpeg_bytes(), "image/jpeg")}).json()
    post_announcement(admin_client, [team.id], media_token=media["token"])
    assert client_for(db, staff).get(media["url"]).status_code == 200
    assert client_for(db, chatter(db, "zed")).get(media["url"]).status_code == 404


def test_announcement_feed_rows_reach_only_recipients(db, admin_client, staff, staff2, team):
    add_member(db, team, staff)
    ravi, sam = client_for(db, staff), client_for(db, staff2)
    c1 = ravi.get("/api/team/bootstrap").json()["cursor"]
    c2 = sam.get("/api/team/bootstrap").json()["cursor"]
    a = post_announcement(admin_client, [team.id]).json()
    assert ravi.get(f"/api/team/updates?since={c1}").json()["changes"]["announcement"] == [
        {"id": a["id"], "action": "created"}
    ]
    assert "announcement" not in sam.get(f"/api/team/updates?since={c2}").json()["changes"]


# --- Kill switches + retention --------------------------------------------------------------------


def test_chat_kill_switch(db, staff_client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "team_chat_enabled", False)
    assert staff_client.get("/api/team/channels").status_code == 403
    assert staff_client.get("/api/team/bootstrap").json()["badges"]["unread"] == 0


def test_retention_deletes_old_and_blanks_deleted(db, staff_client, staff, team):
    add_member(db, team, staff)
    kitchen = kitchen_channel(staff_client, team)
    old = send(staff_client, kitchen["id"], "ancient").json()
    gone = send(staff_client, kitchen["id"], "deleted long ago").json()
    keep = send(staff_client, kitchen["id"], "recent").json()
    staff_client.delete(f"/api/team/messages/{gone['id']}")
    now = utcnow()
    db.get(Message, old["id"]).created_at = now - dt.timedelta(days=400)
    db.get(Message, gone["id"]).deleted_at = now - dt.timedelta(days=31)
    db.commit()

    result = chat.run_chat_retention(db, now=now)
    assert result["deleted"] == 1 and result["blanked"] == 1
    db.expire_all()
    assert db.get(Message, old["id"]) is None
    assert db.get(Message, gone["id"]).body == ""
    assert db.get(Message, keep["id"]).body == "recent"


# --- Review fixes (rev 2 review) ------------------------------------------------------------


def test_block_is_not_revealed_to_the_blocked_person_in_the_feed(db, staff_client, staff):
    from app.team.changes import get_updates

    john = chatter(db, "john")
    john_client = client_for(db, john)
    dm = staff_client.post("/api/team/channels/dm", json={"user_id": john.id}).json()
    cursor = john_client.get("/api/team/bootstrap").json()["cursor"]
    staff_client.post("/api/team/blocks", json={"user_id": john.id})
    # Nothing new for john's devices - not even a "channel updated" row.
    changes = get_updates(db, john, cursor)["changes"]
    assert "channel" not in changes and "dm_block" not in changes
    # He just can't send, with the same neutral answer as always.
    r = send(john_client, dm["id"], "hello?")
    assert r.status_code == 403 and r.json()["detail"] == chat.NEUTRAL_BLOCKED


@pytest.mark.parametrize(
    "body, expected",
    [
        ("thanks @ravi.", {"ravi"}),
        ("@RAVI can you check", {"ravi"}),
        ("ping @Sai Durga please", {"Sai Durga"}),
        ("@Sai look", {"Sai"}),  # "Sai" alone is a different person
        ("@ravikumar is not ravi", set()),
        ("mail me at x@ravi", set()),
        ("@ravi, @Sai Durga!", {"ravi", "Sai Durga"}),
    ],
)
def test_mentions_match_real_usernames(body, expected):
    users = [(1, "ravi"), (2, "Sai Durga"), (3, "Sai")]
    ids = {uid for uid, _ in users if _ in expected}
    assert set(chat.find_mentions(body, users)) == ids


def test_mention_with_space_in_username_reaches_muted_member(db, team, pushes):
    ravi = chatter(db, "ravi")
    sai = chatter(db, "Sai Durga")
    for u in (ravi, sai):
        add_member(db, team, u)
        device(db, u, token=f"tok-{u.id}")
    ravi_c, sai_c = client_for(db, ravi), client_for(db, sai)
    kitchen = kitchen_channel(ravi_c, team)
    sai_c.patch(f"/api/team/channels/{kitchen['id']}/me", json={"muted": True})
    msg = send(ravi_c, kitchen["id"], "@Sai Durga, cooler is open").json()
    assert msg["mentions"] == [sai.id]
    assert pushes[-1]["tokens"] == [f"tok-{sai.id}"]


def test_archiving_a_team_stops_sends_immediately(db, staff_client, staff, team, admin_client):
    add_member(db, team, staff)
    kitchen = kitchen_channel(staff_client, team)
    admin_client.patch(f"/api/team/teams/{team.id}", json={"archived": True})
    # No channel-list reload in between.
    assert send(staff_client, kitchen["id"], "still here?").status_code == 403
    admin_client.patch(f"/api/team/teams/{team.id}", json={"archived": False})
    assert send(staff_client, kitchen["id"], "back").status_code == 200


def test_new_member_does_not_inherit_history_as_unread(db, staff_client, staff, team):
    add_member(db, team, staff)
    kitchen = kitchen_channel(staff_client, team)
    for i in range(5):
        send(staff_client, kitchen["id"], f"old {i}")
    # Joined later: those 5 are history, not unread.
    newbie = chatter(db, "newbie")
    for m in db.query(Message).all():
        m.created_at = utcnow() - dt.timedelta(hours=1)
    db.commit()
    add_member(db, team, newbie)
    newbie_c = client_for(db, newbie)
    assert kitchen_channel(newbie_c, team)["unread"] == 0
    assert newbie_c.get("/api/team/bootstrap").json()["badges"]["unread"] == 0
    # Anything after they joined is unread as usual.
    send(staff_client, kitchen["id"], "welcome!")
    assert kitchen_channel(newbie_c, team)["unread"] == 1


def test_deleted_message_hides_mentions_and_client_uuid(db, team):
    ravi, zed = chatter(db, "ravi"), chatter(db, "zed")
    for u in (ravi, zed):
        add_member(db, team, u)
    c = client_for(db, ravi)
    kitchen = kitchen_channel(c, team)
    msg = send(c, kitchen["id"], "@zed secret", client_uuid="u-1").json()
    out = c.request("DELETE", f"/api/team/messages/{msg['id']}").json()
    assert out["deleted"] and out["mentions"] == [] and out["client_uuid"] is None and out["body"] is None


def test_retention_keeps_evidence_of_an_open_report(db, team):
    ravi, zed = chatter(db, "ravi"), chatter(db, "zed")
    for u in (ravi, zed):
        add_member(db, team, u)
    rc, zc = client_for(db, ravi), client_for(db, zed)
    kitchen = kitchen_channel(rc, team)
    bad = send(rc, kitchen["id"], "@zed nasty").json()
    ok = send(rc, kitchen["id"], "@zed fine").json()
    zc.post(f"/api/team/messages/{bad['id']}/report", json={"reason": "abuse"})
    for mid in (bad["id"], ok["id"]):
        rc.request("DELETE", f"/api/team/messages/{mid}")
    for m in db.query(Message).all():
        m.deleted_at = utcnow() - dt.timedelta(days=40)
    db.commit()
    chat.run_chat_retention(db)
    db.expire_all()
    reported, other = db.get(Message, bad["id"]), db.get(Message, ok["id"])
    assert reported.body == "@zed nasty"  # an admin still has to see it
    assert other.body == "" and other.mentions_json is None


def test_double_report_race_returns_the_first(db, team, monkeypatch):
    from app.team import moderation

    ravi, zed = chatter(db, "ravi"), chatter(db, "zed")
    for u in (ravi, zed):
        add_member(db, team, u)
    kitchen = kitchen_channel(client_for(db, ravi), team)
    msg = send(client_for(db, ravi), kitchen["id"], "hi").json()
    first = moderation.report_message(db, zed, msg["id"], "spam")
    # Simulate the second request missing the "already reported" check.
    real_execute = db.execute
    calls = {"n": 0}

    def execute(stmt, *a, **kw):
        result = real_execute(stmt, *a, **kw)
        if "message_reports" in str(stmt) and calls["n"] == 0:
            calls["n"] += 1

            class Empty:
                def scalars(self):
                    return self

                def first(self):
                    return None

            return Empty()
        return result

    monkeypatch.setattr(db, "execute", execute)
    second = moderation.report_message(db, zed, msg["id"], "spam again")
    assert second["id"] == first["id"]


def test_seen_by_counts_only_current_members(db, team, admin_client):
    ravi, zed = chatter(db, "ravi"), chatter(db, "zed")
    for u in (ravi, zed):
        add_member(db, team, u)
    rc, zc = client_for(db, ravi), client_for(db, zed)
    kitchen = kitchen_channel(rc, team)
    msg = send(rc, kitchen["id"], "hi").json()
    zc.post(f"/api/team/channels/{kitchen['id']}/read", json={"last_read_message_id": msg["id"]})
    assert kitchen_channel(rc, team)["seen_by"] == 1
    admin_client.put(f"/api/team/teams/{team.id}/members", json={"user_ids": [ravi.id]})
    assert kitchen_channel(rc, team)["seen_by"] == 0


def test_admin_viewing_a_dm_sees_both_names_and_cannot_send(db, staff_client, staff, admin_client, caplog):
    john = chatter(db, "john")
    dm = staff_client.post("/api/team/channels/dm", json={"user_id": john.id}).json()
    with caplog.at_level("INFO", logger="app.team.chat_service"):
        out = admin_client.get(f"/api/team/channels/{dm['id']}").json()
    assert out["name"] == "ravi & john" or out["name"] == "john & ravi"
    assert out["can_send"] is False
    assert any("Chat audit" in r.message for r in caplog.records)


def test_announcement_client_uuid_makes_post_idempotent(db, admin_client, staff, team, pushes):
    add_member(db, team, staff)
    device(db, staff)
    body = {"title": "Staff meeting", "body": "Mon 3pm", "team_ids": [team.id], "client_uuid": "a-1"}
    first = admin_client.post("/api/team/announcements", json=body).json()
    again = admin_client.post("/api/team/announcements", json=body).json()
    assert again["id"] == first["id"]
    assert len(admin_client.get("/api/team/announcements").json()["announcements"]) == 1
    assert len([p for p in pushes if p["data"].get("route") == "announcement"]) == 1


def test_remind_now_clears_a_queued_night_remind(db, admin_client, admin, staff, team, pushes, monkeypatch):
    add_member(db, team, staff)
    device(db, staff)
    a = admin_client.post(
        "/api/team/announcements", json={"title": "Read me", "team_ids": [team.id], "require_ack": True}
    ).json()
    monkeypatch.setattr(ann, "in_quiet_hours", lambda: True)
    admin_client.post(f"/api/team/announcements/{a['id']}/remind")  # queued remind:1
    monkeypatch.setattr(ann, "in_quiet_hours", lambda: False)
    admin_client.post(f"/api/team/announcements/{a['id']}/remind")  # remind:2 now
    before = len(pushes)
    ann.flush_pending_pushes(db)
    assert len(pushes) == before  # remind:1 isn't sent on top


def test_failed_queued_push_is_retried(db, admin_client, staff, team, monkeypatch):
    add_member(db, team, staff)
    device(db, staff)
    monkeypatch.setattr(ann, "in_quiet_hours", lambda: True)
    a = admin_client.post("/api/team/announcements", json={"title": "Night post", "team_ids": [team.id]}).json()
    monkeypatch.setattr(ann, "in_quiet_hours", lambda: False)

    class Failed:
        delivered_tokens, invalid_tokens, success_count = [], [], 0

    monkeypatch.setattr(chat.push, "is_configured", lambda: True)
    monkeypatch.setattr(chat.push, "send_to_tokens", lambda *a, **k: Failed())
    ann.flush_pending_pushes(db)
    db.expire_all()
    from app.team.chat_models import Announcement

    assert db.get(Announcement, a["id"]).pending_push == "post"  # kept for the next run


def test_everyone_exclusion_keeps_demo_login_out_of_everyone_chat(db, staff_client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "team_everyone_exclude", "appreview")
    demo = chatter(db, "appreview")
    demo_c = client_for(db, demo)
    assert [c["name"] for c in demo_c.get("/api/team/channels").json()["channels"]] == []
    everyone = next(c for c in staff_client.get("/api/team/channels").json()["channels"] if c["name"] == "Everyone")
    assert demo_c.get(f"/api/team/channels/{everyone['id']}/messages").status_code == 404
    assert demo.id not in [m["id"] for m in staff_client.get(f"/api/team/channels/{everyone['id']}/members").json()["members"]]
