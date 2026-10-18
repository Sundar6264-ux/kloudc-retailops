"""Team hub Phase 0: app config, bootstrap, teams admin, the change feed and
image uploads (docs/TEAM_HUB_DESIGN.md §6-§9)."""

import datetime as dt
import io
import pathlib

from PIL import Image

from app.config import get_settings
from app.team.changes import get_updates, prune_team_changes, record_change
from app.team.models import TeamChange, TeamMedia
from app.team import media as media_svc
from tests.conftest import add_member, client_for, make_user


# --- /api/app/config ----------------------------------------------------------


def test_app_config_needs_no_login(anon_client):
    r = anon_client.get("/api/app/config")
    assert r.status_code == 200
    body = r.json()
    assert body["min_supported_build"] == {"ios": 0, "android": 0}
    assert set(body["store_url"]) == {"ios", "android"}


# --- bootstrap ------------------------------------------------------------------


def test_bootstrap_requires_login(anon_client):
    assert anon_client.get("/api/team/bootstrap").status_code == 401


def test_bootstrap_shape(db, staff_client, team):
    add_member(db, team, staff_client.user)
    body = staff_client.get("/api/team/bootstrap").json()
    assert set(body) == {
        "teams", "my_team_ids", "can_assign", "features", "flags",
        "min_supported_build", "cursor", "badges",
    }
    names = [t["name"] for t in body["teams"]]
    assert names[0] == "Everyone"
    assert set(names) == {"Everyone", "Grocery", "Kitchen", "Managers"}
    everyone = next(t["id"] for t in body["teams"] if t["is_everyone"])
    assert set(body["my_team_ids"]) == {everyone, team.id}
    assert body["can_assign"] is False
    assert body["features"] == {"team_tasks": True, "team_chat": True}
    assert body["flags"]["poll_interval_s"] == 30
    assert body["flags"]["chat_poll_interval_s"] == 5
    assert body["flags"]["badge_poll_interval_s"] == 120
    assert body["badges"] == {"tasks_due": 0, "unread": 0, "announcements_unacked": 0}
    assert isinstance(body["cursor"], int)


def test_bootstrap_features_follow_grants(db, staff2_client):
    body = staff2_client.get("/api/team/bootstrap").json()
    assert body["features"] == {"team_tasks": True, "team_chat": False}


def test_bootstrap_hides_archived_teams(db, admin_client, staff_client, team):
    admin_client.patch(f"/api/team/teams/{team.id}", json={"archived": True})
    names = [t["name"] for t in staff_client.get("/api/team/bootstrap").json()["teams"]]
    assert "Kitchen" not in names


# --- can_assign ---------------------------------------------------------------------


def test_can_assign_admin_only_and_in_user_list(db, admin_client, staff_client, staff):
    assert staff_client.patch(f"/api/auth/users/{staff.id}/can-assign", json={"can_assign": True}).status_code == 403
    r = admin_client.patch(f"/api/auth/users/{staff.id}/can-assign", json={"can_assign": True})
    assert r.status_code == 200 and r.json()["can_assign"] is True
    rows = {u["username"]: u for u in admin_client.get("/api/auth/users").json()}
    assert rows["ravi"]["can_assign"] is True
    assert staff_client.get("/api/team/bootstrap").json()["can_assign"] is True
    assert admin_client.patch("/api/auth/users/9999/can-assign", json={"can_assign": True}).status_code == 404


def test_admin_always_can_assign(admin_client):
    assert admin_client.get("/api/team/bootstrap").json()["can_assign"] is True


# --- teams admin ----------------------------------------------------------------------


def test_non_admin_cannot_manage_teams(staff_client, team):
    assert staff_client.get("/api/team/teams").status_code == 403
    assert staff_client.post("/api/team/teams", json={"name": "Office"}).status_code == 403
    assert staff_client.patch(f"/api/team/teams/{team.id}", json={"name": "X"}).status_code == 403
    assert staff_client.put(f"/api/team/teams/{team.id}/members", json={"user_ids": [1]}).status_code == 403


def test_teams_crud(db, admin_client, staff, staff2):
    r = admin_client.post("/api/team/teams", json={"name": " Office ", "color": "#aabbcc"})
    assert r.status_code == 200
    office = r.json()
    assert office["name"] == "Office" and office["color"] == "#AABBCC" and office["member_ids"] == []

    assert admin_client.post("/api/team/teams", json={"name": "office"}).status_code == 409
    assert admin_client.post("/api/team/teams", json={"name": ""}).status_code == 400
    assert admin_client.post("/api/team/teams", json={"name": "Bad", "color": "red"}).status_code == 400

    r = admin_client.put(f"/api/team/teams/{office['id']}/members", json={"user_ids": [staff.id, staff2.id]})
    assert r.json()["member_ids"] == sorted([staff.id, staff2.id])
    r = admin_client.put(f"/api/team/teams/{office['id']}/members", json={"user_ids": [staff2.id]})
    assert r.json()["member_ids"] == [staff2.id]
    assert admin_client.put(f"/api/team/teams/{office['id']}/members", json={"user_ids": [424242]}).status_code == 400

    r = admin_client.patch(f"/api/team/teams/{office['id']}", json={"name": "Back office", "archived": True})
    assert r.json()["name"] == "Back office" and r.json()["archived"] is True
    assert admin_client.patch("/api/team/teams/9999", json={"name": "x"}).status_code == 404


def test_everyone_team_rules(db, admin_client, staff, staff2):
    teams = admin_client.get("/api/team/teams").json()
    everyone = next(t for t in teams if t["is_everyone"])
    # Implicit membership: every user, no rows needed.
    assert set(everyone["member_ids"]) >= {staff.id, staff2.id, admin_client.user.id}
    assert admin_client.put(f"/api/team/teams/{everyone['id']}/members", json={"user_ids": []}).status_code == 400
    assert admin_client.patch(f"/api/team/teams/{everyone['id']}", json={"archived": True}).status_code == 400


def test_deleting_a_user_removes_memberships(db, admin_client, staff, team):
    add_member(db, team, staff)
    assert admin_client.delete(f"/api/auth/users/{staff.id}").status_code == 200
    kitchen = next(t for t in admin_client.get("/api/team/teams").json() if t["id"] == team.id)
    assert kitchen["member_ids"] == []


# --- change feed ------------------------------------------------------------------------


def _record(db, *args, **kwargs):
    record_change(db, *args, **kwargs)
    db.commit()


def test_updates_exactly_after_cursor(db, staff_client, staff):
    start = staff_client.get("/api/team/updates?since=0").json()["cursor"]
    _record(db, "task", 1, "created", user_ids=[staff.id])
    mid = staff_client.get(f"/api/team/updates?since={start}").json()
    assert mid["changes"] == {"task": [{"id": 1, "action": "created"}]}
    _record(db, "task", 2, "created", user_ids=[staff.id])
    after = staff_client.get(f"/api/team/updates?since={mid['cursor']}").json()
    assert after["changes"] == {"task": [{"id": 2, "action": "created"}]}, "nothing from before the cursor"
    again = staff_client.get(f"/api/team/updates?since={after['cursor']}").json()
    assert again["changes"] == {} and again["cursor"] == after["cursor"]


def test_updates_latest_action_wins_and_tombstones(db, staff_client, staff):
    since = staff_client.get("/api/team/updates?since=0").json()["cursor"]
    _record(db, "task", 5, "created", user_ids=[staff.id])
    _record(db, "task", 5, "updated", user_ids=[staff.id])
    _record(db, "task", 5, "deleted", user_ids=[staff.id])
    body = staff_client.get(f"/api/team/updates?since={since}").json()
    assert body["changes"] == {"task": [{"id": 5, "action": "deleted"}]}


def test_updates_visibility(db, staff_client, staff2_client, admin_client, staff, staff2, team):
    add_member(db, team, staff)
    since = admin_client.get("/api/team/updates?since=0").json()["cursor"]
    _record(db, "task", 10, "created", team_id=team.id)  # Kitchen only
    _record(db, "task", 11, "created", user_ids=[staff2.id])  # Sam only
    _record(db, "team", 3, "updated")  # everyone
    _record(db, "message", 12, "created", channel_id=77)  # channel members only

    ravi = staff_client.get(f"/api/team/updates?since={since}").json()["changes"]
    assert ravi == {"task": [{"id": 10, "action": "created"}], "team": [{"id": 3, "action": "updated"}]}
    sam = staff2_client.get(f"/api/team/updates?since={since}").json()["changes"]
    assert sam == {"task": [{"id": 11, "action": "created"}], "team": [{"id": 3, "action": "updated"}]}
    boss = admin_client.get(f"/api/team/updates?since={since}").json()["changes"]
    assert {c["id"] for c in boss["task"]} == {10, 11}
    assert "message" not in boss, "admins see channel rows only as members"


def test_updates_channel_resolver_hook(db, staff_client, staff):
    from app.team import changes

    saved = list(changes._channel_resolvers)
    changes._channel_resolvers.clear()  # only this test's resolver
    changes.register_channel_resolver(lambda _db, user: {77} if user.id == staff.id else set())
    try:
        since = staff_client.get("/api/team/updates?since=0").json()["cursor"]
        _record(db, "message", 12, "created", channel_id=77)
        body = staff_client.get(f"/api/team/updates?since={since}").json()
        assert body["changes"] == {"message": [{"id": 12, "action": "created"}]}
    finally:
        changes._channel_resolvers[:] = saved


def test_updates_resync_after_prune(db, staff_client, staff):
    _record(db, "task", 1, "created", user_ids=[staff.id])
    _record(db, "task", 2, "created", user_ids=[staff.id])
    old_cursor = 0
    # Age the first row past the retention window, then prune it.
    first = db.query(TeamChange).order_by(TeamChange.seq).first()
    first.created_at = first.created_at - dt.timedelta(days=get_settings().team_changes_retention_days + 1)
    first_seq = first.seq
    db.commit()
    assert prune_team_changes(db) == 1
    body = staff_client.get(f"/api/team/updates?since={old_cursor}").json()
    assert body["resync_required"] is True
    # A cursor at or after the pruned range is still answered exactly.
    fresh = staff_client.get(f"/api/team/updates?since={first_seq}").json()
    assert fresh["resync_required"] is False
    assert fresh["changes"] == {"task": [{"id": 2, "action": "created"}]}


def test_updates_cursor_survives_pruning_everything(db, staff_client, staff):
    _record(db, "task", 1, "created", user_ids=[staff.id])
    cursor = staff_client.get("/api/team/updates?since=0").json()["cursor"]
    db.query(TeamChange).delete()
    db.commit()
    body = staff_client.get(f"/api/team/updates?since={cursor}").json()
    assert body["resync_required"] is False and body["cursor"] == cursor
    assert staff_client.get("/api/team/updates?since=0").json()["resync_required"] is True


def test_updates_future_cursor_resyncs(staff_client):
    assert staff_client.get("/api/team/updates?since=999999").json()["resync_required"] is True


def test_team_changes_recorded_for_admin_edits(db, admin_client, staff_client, team):
    since = staff_client.get("/api/team/updates?since=0").json()["cursor"]
    admin_client.put(f"/api/team/teams/{team.id}/members", json={"user_ids": [staff_client.user.id]})
    body = staff_client.get(f"/api/team/updates?since={since}").json()
    assert body["changes"] == {"team": [{"id": team.id, "action": "updated"}]}


def test_record_change_rejects_unknown_types(db):
    import pytest

    with pytest.raises(ValueError):
        record_change(db, "banana", 1, "created")
    with pytest.raises(ValueError):
        record_change(db, "task", 1, "exploded")


def test_record_change_rolls_back_with_its_transaction(db, staff):
    before = db.query(TeamChange).count()
    record_change(db, "task", 1, "created", user_ids=[staff.id])
    db.rollback()
    assert db.query(TeamChange).count() == before


# --- media --------------------------------------------------------------------------------


def _jpeg_with_gps(size=(64, 48)):
    img = Image.new("RGB", size, (200, 30, 30))
    exif = Image.Exif()
    exif[0x010F] = "SpyCam"  # Make
    exif[0x8825] = {1: "N", 2: (41.0, 50.0, 0.0)}  # GPS IFD
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def _png_rgba():
    img = Image.new("RGBA", (40, 40), (0, 0, 255, 128))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _upload(client, data, name="photo.jpg", ctype="image/jpeg"):
    return client.post("/api/team/media", files={"file": (name, data, ctype)})


def test_upload_jpeg_reencoded_without_exif(db, staff_client):
    r = _upload(staff_client, _jpeg_with_gps(), name="../../etc/passwd.jpg")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url"] == f"/api/team/media/{body['token']}"
    media = db.query(TeamMedia).one()
    stored = pathlib.Path(get_settings().team_uploads_dir) / media.path
    assert stored.is_file()
    assert "passwd" not in media.path and media.path.endswith(".jpg")
    assert media.token not in media.path, "the access token isn't the file name"
    with Image.open(stored) as img:
        assert img.format == "JPEG"
        assert len(img.getexif()) == 0, "EXIF/GPS stripped"


def test_upload_png_with_alpha_becomes_jpeg(staff_client):
    r = _upload(staff_client, _png_rgba(), name="x.png", ctype="image/png")
    assert r.status_code == 200
    got = staff_client.get(r.json()["url"])
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"


def test_upload_heic(staff_client):
    if not media_svc.HEIC_SUPPORTED:
        return
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (0, 128, 0)).save(buf, format="HEIF")
    r = _upload(staff_client, buf.getvalue(), name="IMG_1.HEIC", ctype="image/heic")
    assert r.status_code == 200, r.text


def test_upload_large_image_is_resized(staff_client):
    buf = io.BytesIO()
    Image.new("RGB", (4000, 1000), (10, 10, 10)).save(buf, format="JPEG")
    body = _upload(staff_client, buf.getvalue()).json()
    assert max(body["width"], body["height"]) == media_svc.MAX_SIDE


def test_upload_rejects_wrong_declared_type(staff_client):
    assert _upload(staff_client, _jpeg_with_gps(), name="x.pdf", ctype="application/pdf").status_code == 415


def test_upload_rejects_fake_image(staff_client):
    assert _upload(staff_client, b"%PDF-1.4 not an image at all").status_code == 415
    assert _upload(staff_client, b"").status_code == 415


def test_upload_rejects_truncated_image(staff_client):
    data = _jpeg_with_gps((400, 400))
    assert _upload(staff_client, data[: len(data) // 3]).status_code == 415


def test_upload_rejects_disallowed_real_format(staff_client):
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="GIF")
    # Declared as JPEG, but it's really a GIF.
    assert _upload(staff_client, buf.getvalue()).status_code == 415


def test_upload_rejects_too_large(staff_client):
    big = b"\xff\xd8" + b"0" * (media_svc.MAX_UPLOAD_BYTES + 10)
    assert _upload(staff_client, big).status_code == 413


def test_upload_rejects_decompression_bomb(staff_client):
    buf = io.BytesIO()
    # Tiny file, 400 megapixels once decoded.
    Image.new("1", (20000, 20000)).save(buf, format="PNG", optimize=True)
    assert len(buf.getvalue()) < media_svc.MAX_UPLOAD_BYTES
    assert _upload(staff_client, buf.getvalue(), name="b.png", ctype="image/png").status_code == 415


def test_upload_requires_login(anon_client):
    assert _upload(anon_client, _jpeg_with_gps()).status_code == 401


def test_media_access(db, staff_client, staff2_client, admin_client, anon_client):
    url = _upload(staff_client, _jpeg_with_gps()).json()["url"]
    assert staff_client.get(url).status_code == 200, "uploader"
    assert admin_client.get(url).status_code == 200, "admin"
    assert staff2_client.get(url).status_code == 404, "unattached: nobody else"
    assert anon_client.get(url).status_code == 401
    assert staff_client.get("/api/team/media/not-a-token").status_code == 404


def test_media_access_hook(db, staff_client, staff2_client, staff, staff2):
    token = _upload(staff_client, _jpeg_with_gps()).json()["token"]
    saved = dict(media_svc._access_checks)
    media_svc.register_media_access("task", lambda _db, user, owner_id: owner_id == 5 and user.id == staff2.id)
    try:
        media_svc.attach_media(db, staff, token, "task", 5)
        db.commit()
        assert staff2_client.get(f"/api/team/media/{token}").status_code == 200
        other = make_user(db, "john")
        assert client_for(db, other).get(f"/api/team/media/{token}").status_code == 404
    finally:
        # Put back the real hooks (tasks/chat register theirs at import) so
        # later test files still see them.
        media_svc._access_checks.clear()
        media_svc._access_checks.update(saved)


def test_attach_media_only_own_upload(db, staff_client, staff2):
    import pytest
    from fastapi import HTTPException

    token = _upload(staff_client, _jpeg_with_gps()).json()["token"]
    with pytest.raises(HTTPException):
        media_svc.attach_media(db, staff2, token, "task", 1)


# --- review fixes: ids never reused, account deletion, Everyone exclusion ---------------


def test_deleted_users_id_is_never_given_to_a_new_account(db, admin_client):
    made = admin_client.post(
        "/api/auth/users", json={"username": "leaver", "password": "pw123456", "allowed_features": []}
    ).json()
    assert admin_client.delete(f"/api/auth/users/{made['id']}").status_code == 200
    again = admin_client.post(
        "/api/auth/users", json={"username": "newhire", "password": "pw123456", "allowed_features": []}
    ).json()
    assert again["id"] > made["id"], "the new hire must not inherit the leaver's id"


def test_deleting_a_user_cleans_up_what_would_keep_acting_for_them(db, admin_client, admin, staff, staff2):
    import json

    from app.team.chat_models import Channel, ChannelMember, DmBlock
    from app.team.tasks_models import Task, TaskEvent, TaskTemplate
    from app.timeutil import utcnow

    now = utcnow()
    dm = Channel(kind="dm", dm_key=f"{staff.id}:{staff2.id}", created_at=now)
    db.add(dm)
    db.flush()
    db.add_all(
        [
            ChannelMember(channel_id=dm.id, user_id=staff.id),
            ChannelMember(channel_id=dm.id, user_id=staff2.id),
            DmBlock(blocker_user_id=staff2.id, blocked_user_id=staff.id, created_at=now),
        ]
    )
    common = dict(created_by_user_id=admin.id, created_by_name="boss", created_at=now, updated_at=now)
    open_task = Task(title="Restock rice", assignee_user_id=staff.id, status="open", **common)
    done_task = Task(title="Old", assignee_user_id=staff.id, status="done", **common)
    tpl = TaskTemplate(
        title="Clean cooler", assignee_user_id=staff.id, rrule="FREQ=DAILY;BYHOUR=15", active=True, **common
    )
    db.add_all([open_task, done_task, tpl])
    db.commit()
    token = _upload(client_for(db, staff), _jpeg_with_gps()).json()["token"]
    staff_id = staff.id

    assert admin_client.delete(f"/api/auth/users/{staff_id}").status_code == 200
    db.expire_all()

    assert db.query(ChannelMember).filter_by(user_id=staff_id).count() == 0
    assert db.query(DmBlock).count() == 0, "blocks either way are gone"
    assert db.get(Task, open_task.id).status == "cancelled"
    assert db.get(Task, done_task.id).status == "done", "history is kept"
    event = db.query(TaskEvent).filter_by(task_id=open_task.id, kind="cancelled").one()
    assert "removed" in json.loads(event.detail_json)["reason"]
    assert db.get(TaskTemplate, tpl.id).active is False
    assert db.query(TeamMedia).filter_by(token=token).one().uploaded_by_user_id is None
    # The creator's app hears about the cancelled task.
    assert get_updates(db, admin, 0)["changes"].get("task")


def test_everyone_excludes_listed_logins(db, admin_client):
    from app.team.changes import user_team_ids

    reviewer = make_user(db, "App_Store_Review", features=["team_chat"])
    regular = make_user(db, "ravi2", features=["team_chat"])
    everyone = next(t for t in admin_client.get("/api/team/teams").json() if t["is_everyone"])
    assert regular.id in everyone["member_ids"]
    assert reviewer.id not in everyone["member_ids"], "case-insensitive match"
    everyone_id = everyone["id"]
    assert everyone_id not in user_team_ids(db, reviewer)
    assert everyone_id in user_team_ids(db, regular)
    boot = client_for(db, reviewer).get("/api/team/bootstrap").json()
    assert everyone_id not in boot["my_team_ids"]


# --- review fixes: uploads --------------------------------------------------------------


def _jpeg_bytes(img, **kwargs):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", **kwargs)
    return buf.getvalue()


def test_upload_accepts_hdr_mpo_jpeg(staff_client):
    buf = io.BytesIO()
    first = Image.new("RGB", (64, 48), (1, 2, 3))
    first.save(buf, format="MPO", save_all=True, append_images=[Image.new("RGB", (64, 48), (9, 9, 9))])
    with Image.open(io.BytesIO(buf.getvalue())) as probe:
        assert probe.format == "MPO"
    r = _upload(staff_client, buf.getvalue())
    assert r.status_code == 200, r.text


def test_upload_drops_jpeg_comment_and_icc(db, staff_client):
    img = Image.new("RGB", (40, 30), (5, 5, 5))
    data = _jpeg_bytes(img, comment=b"GPS 40.7,-74.0 secret", icc_profile=b"\x00" * 128)
    assert _upload(staff_client, data).status_code == 200
    media = db.query(TeamMedia).one()
    stored = (pathlib.Path(get_settings().team_uploads_dir) / media.path).read_bytes()
    assert b"secret" not in stored
    with Image.open(io.BytesIO(stored)) as out:
        assert "comment" not in out.info and "icc_profile" not in out.info


def test_upload_odd_modes_are_accepted(staff_client):
    cases = []
    for mode, fmt, ctype in (
        ("LA", "PNG", "image/png"),
        ("I;16", "PNG", "image/png"),
        ("CMYK", "JPEG", "image/jpeg"),
        ("L", "JPEG", "image/jpeg"),
    ):
        buf = io.BytesIO()
        Image.new(mode, (3000, 20)).save(buf, format=fmt)
        cases.append((mode, buf.getvalue(), ctype))
    pal = Image.new("P", (50, 50))
    pal.info["transparency"] = 0
    buf = io.BytesIO()
    pal.save(buf, format="PNG", transparency=0)
    cases.append(("P+alpha", buf.getvalue(), "image/png"))
    for mode, data, ctype in cases:
        r = _upload(staff_client, data, name="x", ctype=ctype)
        assert r.status_code == 200, (mode, r.text)
        assert max(r.json()["width"], r.json()["height"]) <= media_svc.MAX_SIDE


def test_big_image_is_shrunk_before_colour_conversion(monkeypatch):
    """The full-size image is never converted: only the thumbnail is."""
    seen = []
    real_convert = Image.Image.convert

    def spy(self, mode=None, *args, **kwargs):
        seen.append((self.size, mode))
        return real_convert(self, mode, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "convert", spy)
    buf = io.BytesIO()
    Image.new("RGBA", (5000, 5000), (0, 0, 255, 128)).save(buf, format="PNG")
    jpeg, w, h = media_svc.process_image(buf.getvalue(), "image/png")
    assert (w, h) == (media_svc.MAX_SIDE, media_svc.MAX_SIDE)
    full = [mode for size, mode in seen if size == (5000, 5000)]
    # Only Pillow's own alpha premultiply (inside thumbnail) sees full size;
    # the RGBA/RGB conversion and flattening happen on the thumbnail.
    assert full == ["RGBa"], seen


def test_upload_refused_by_content_length_before_parsing(staff_client, monkeypatch):
    parsed = []
    from starlette.requests import Request

    real_form = Request.form

    def spy(self, *args, **kwargs):
        parsed.append(True)
        return real_form(self, *args, **kwargs)

    monkeypatch.setattr(Request, "form", spy)
    big = b"\xff\xd8" + b"0" * (media_svc.MAX_REQUEST_BYTES + 10)
    r = _upload(staff_client, big)
    assert r.status_code == 413
    assert parsed == [], "the multipart body was never parsed"


def test_upload_without_content_length_is_refused(staff_client):
    def body():
        yield b"--x\r\n"

    r = staff_client.post(
        "/api/team/media", content=body(), headers={"content-type": "multipart/form-data; boundary=x"}
    )
    assert r.status_code == 411


def test_upload_needs_team_access(db):
    outsider = client_for(db, make_user(db, "cashier", features=["open_orders"]))
    assert _upload(outsider, _jpeg_with_gps()).status_code == 403


def test_media_served_with_nosniff(staff_client):
    url = _upload(staff_client, _jpeg_with_gps()).json()["url"]
    assert staff_client.get(url).headers["x-content-type-options"] == "nosniff"


def test_failed_commit_leaves_no_file(db, staff, monkeypatch):
    import pytest
    from fastapi import UploadFile

    root = pathlib.Path(get_settings().team_uploads_dir)
    before = set(root.rglob("*")) if root.exists() else set()

    def boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "commit", boom)
    upload = UploadFile(file=io.BytesIO(_jpeg_with_gps()), filename="a.jpg", headers={"content-type": "image/jpeg"})
    with pytest.raises(RuntimeError):
        media_svc.save_upload(db, staff, upload)
    after = set(root.rglob("*")) if root.exists() else set()
    assert {p for p in after if p.is_file()} <= before, "no stray file or temp file"


def test_prune_unattached_media(db, staff_client, staff):
    old_token = _upload(staff_client, _jpeg_with_gps()).json()["token"]
    kept_token = _upload(staff_client, _jpeg_with_gps()).json()["token"]
    fresh_token = _upload(staff_client, _jpeg_with_gps()).json()["token"]
    old, kept = (db.query(TeamMedia).filter_by(token=t).one() for t in (old_token, kept_token))
    long_ago = old.created_at - dt.timedelta(days=2)
    old.created_at = long_ago
    kept.created_at = long_ago
    kept.owner_type, kept.owner_id = "task", 1
    db.commit()
    old_path = media_svc.media_file(old)

    assert media_svc.prune_unattached_media(db) == 1
    tokens = {m.token for m in db.query(TeamMedia).all()}
    assert tokens == {kept_token, fresh_token}
    assert not old_path.exists()
