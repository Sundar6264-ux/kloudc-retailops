"""Verify 3 (App Review 1.2, docs/BATCH_OCT14_CONTRACT.md "As built - Verify 3"):
blocking someone hides their messages everywhere the person who blocked
looks - message search included, not only the conversation and the chat
list. The person blocked still finds the blocker's messages (nothing tells
them they were blocked)."""

import uuid

from tests.conftest import add_member, client_for, make_user


def _chatter(db, name):
    return make_user(db, name, features=["team_chat", "team_tasks"])


def _kitchen(client, team):
    channels = client.get("/api/team/channels").json()["channels"]
    return next(c for c in channels if c["team_id"] == team.id)


def _send(client, channel_id, body):
    r = client.post(
        f"/api/team/channels/{channel_id}/messages",
        json={"body": body, "client_uuid": str(uuid.uuid4())},
    )
    assert r.status_code == 200, r.text
    return r.json()


def _hits(client, q, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    r = client.get(f"/api/team/messages/search?q={q}" + (f"&{query}" if query else ""))
    assert r.status_code == 200, r.text
    return [m["id"] for m in r.json()["results"]]


def test_search_leaves_out_people_i_blocked_in_team_chats(db, team):
    anna, bob, carl = _chatter(db, "anna"), _chatter(db, "bob"), _chatter(db, "carl")
    for u in (anna, bob, carl):
        add_member(db, team, u)
    ac, bc, cc = client_for(db, anna), client_for(db, bob), client_for(db, carl)
    ch = _kitchen(ac, team)
    from_bob = _send(bc, ch["id"], "cooler is broken again")
    from_carl = _send(cc, ch["id"], "cooler fixed")
    mine = _send(ac, ch["id"], "thanks for the cooler")
    assert _hits(ac, "cooler") == [mine["id"], from_carl["id"], from_bob["id"]]

    assert ac.post("/api/team/blocks", json={"user_id": bob.id}).status_code == 200
    # Everywhere: all chats, and within the team chat.
    assert _hits(ac, "cooler") == [mine["id"], from_carl["id"]]
    assert _hits(ac, "cooler", channel_id=ch["id"]) == [mine["id"], from_carl["id"]]
    # Only for the one who blocked.
    assert _hits(bc, "cooler") == [mine["id"], from_carl["id"], from_bob["id"]]
    assert _hits(cc, "cooler") == [mine["id"], from_carl["id"], from_bob["id"]]

    # Unblock brings them back.
    assert ac.delete(f"/api/team/blocks/{bob.id}").status_code in (200, 204)
    assert _hits(ac, "cooler") == [mine["id"], from_carl["id"], from_bob["id"]]
