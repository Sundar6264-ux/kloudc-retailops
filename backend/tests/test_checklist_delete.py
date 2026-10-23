"""Deleting a checklist (owner, Oct 22: "i created one checklist by mistake and
am not able to delete that" - "but only admins can delete").

DELETE /api/checklists/admin/definitions/{checklist_type}: admins only (a
"Build checklists" user gets 403 and can still turn it off), never the App
Review demo login, only a checklist with no submissions at all (409 with how
many otherwise), never a built-in one. Everything that points at it goes:
versions, draft, public link (its URL stops working), WhatsApp group choice
and automatic tasks.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import ChecklistDefinition, ChecklistDefinitionVersion, ChecklistPublicLink, ChecklistSubmission
from app.routers.checklist_admin import ADMIN_ONLY_DELETE
from app.services import checklist_admin as svc
from app.services import checklist_public as public_svc
from app.team.models import TeamMedia
from app.team.tasks_models import Task, TaskEvent
from app.timeutil import utcnow
from tests.conftest import client_for, make_user

DEFS = "/api/checklists/admin/definitions"
PUBLIC = "/api/public/checklists"


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    public_svc.limiter.reset()
    monkeypatch.setattr(public_svc, "IP_LIMIT", (10000, 600))
    monkeypatch.setattr(public_svc, "IP_PAGE_LIMIT", (10000, 600))
    monkeypatch.setattr(public_svc, "FAILED_IP_LIMIT", (10000, 600))
    yield
    public_svc.limiter.reset()


def _published(client, title="Deli opening"):
    r = client.post(DEFS, json={"title": title})
    assert r.status_code == 200, r.text
    ctype = r.json()["checklist_type"]
    item = {"id": "i1", "type": "short_text", "title": "Lights on?", "required": True}
    r = client.put(f"{DEFS}/{ctype}", json={"sections": [{"id": "s1", "title": "Open", "items": [item]}]})
    assert r.status_code == 200, r.text
    assert client.post(f"{DEFS}/{ctype}/publish", json={}).status_code == 200
    return ctype


def _submission(db, ctype, user_id=1, name="chef"):
    db.add(
        ChecklistSubmission(
            checklist_type=ctype,
            submitted_by_user_id=user_id,
            submitted_by_name=name,
            answers_json="{}",
            all_clear=True,
            answers_hash=f"h-{ctype}-{user_id}-{name}",
            created_at=utcnow(),
            definition_version=1,
        )
    )
    db.commit()


def _row(db, ctype):
    db.expire_all()
    return db.query(ChecklistDefinition).filter_by(checklist_type=ctype).one_or_none()


def test_a_never_used_checklist_is_deleted_with_everything_linked(db, admin_client, team):
    ctype = _published(admin_client)
    # A second, unpublished draft on top, a WhatsApp group, a public link.
    item = {"id": "i1", "type": "short_text", "title": "Lights and fans on?"}
    assert admin_client.put(f"{DEFS}/{ctype}", json={"sections": [{"id": "s1", "items": [item]}]}).status_code == 200
    assert admin_client.patch(f"{DEFS}/{ctype}", json={"settings": {"whatsapp_group": "store"}}).status_code == 200
    link = admin_client.put(f"{DEFS}/{ctype}/public-link", json={"enabled": True, "name_mode": "free_text"})
    assert link.status_code == 200, link.text
    link = link.json()
    key = link["path"][len("/c/"):]
    anon = TestClient(app)
    assert anon.get(f"{PUBLIC}/{key}").status_code == 200

    definition = _row(db, ctype)
    assert definition.draft_json and db.query(ChecklistDefinitionVersion).filter_by(definition_id=definition.id).count() == 1
    # An automatic "Submit the ... checklist" task for it (and one for another
    # checklist whose name starts the same, which must stay).
    now = utcnow()
    for source in (f"{ctype}:2026-10-22", f"{ctype}x:2026-10-22"):
        db.add(
            Task(
                title="Submit it",
                assignee_team_id=team.id,
                status="open",
                source_type="checklist",
                source_id=source,
                created_by_user_id=0,
                created_by_name="Store",
                created_at=now,
                updated_at=now,
            )
        )
    db.commit()
    task_id = db.query(Task).filter_by(source_id=f"{ctype}:2026-10-22").one().id
    db.add(TaskEvent(task_id=task_id, user_name="Store", kind="created", created_at=now))
    # A picture only this checklist used.
    db.add(
        TeamMedia(
            token="tok_only_this_one_123", path="x.jpg", bytes=1, width=1, height=1,
            owner_type=svc.MEDIA_OWNER_TYPE, owner_id=definition.id, created_at=now,
        )
    )
    db.commit()
    definition_id = definition.id

    listed = {d["checklist_type"]: d for d in admin_client.get(DEFS).json()["definitions"]}
    assert listed[ctype]["can_delete"] is True and listed[ctype]["builtin"] is False
    assert admin_client.get(f"{DEFS}/{ctype}").json()["can_delete"] is True

    r = admin_client.delete(f"{DEFS}/{ctype}")
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": True, "checklist_type": ctype, "title": "Deli opening"}

    assert _row(db, ctype) is None
    assert db.query(ChecklistDefinitionVersion).filter_by(definition_id=definition_id).count() == 0
    assert db.query(ChecklistPublicLink).filter_by(checklist_type=ctype).count() == 0
    assert db.query(Task).filter_by(source_id=f"{ctype}:2026-10-22").count() == 0
    assert db.query(TaskEvent).filter_by(task_id=task_id).count() == 0
    assert db.query(Task).filter_by(source_id=f"{ctype}x:2026-10-22").count() == 1
    media = db.query(TeamMedia).filter_by(token="tok_only_this_one_123").one()
    assert media.owner_type is None and media.owner_id is None  # the nightly prune removes it
    # Gone everywhere: the builder, staff's list, the old public link.
    assert ctype not in {d["checklist_type"] for d in admin_client.get(DEFS).json()["definitions"]}
    assert admin_client.get(f"{DEFS}/{ctype}").status_code == 404
    assert anon.get(f"{PUBLIC}/{key}").status_code == 404
    assert admin_client.delete(f"{DEFS}/{ctype}").status_code == 404


def test_a_draft_only_checklist_can_be_deleted(db, admin_client):
    r = admin_client.post(DEFS, json={"title": "Oops"})
    ctype = r.json()["checklist_type"]
    assert admin_client.delete(f"{DEFS}/{ctype}").status_code == 200
    assert _row(db, ctype) is None


def test_a_picture_a_copy_still_shows_moves_to_the_copy(db, admin_client):
    ctype = _published(admin_client, "With picture")
    definition = _row(db, ctype)
    copy_type = admin_client.post(f"{DEFS}/{ctype}/duplicate", json={}).json()["checklist_type"]
    copy = _row(db, copy_type)
    copy.draft_json = '{"sections": [{"items": [{"type": "image", "image_url": "/api/team/media/tok_shared_picture_1"}]}]}'
    db.add(
        TeamMedia(
            token="tok_shared_picture_1", path="y.jpg", bytes=1, width=1, height=1,
            owner_type=svc.MEDIA_OWNER_TYPE, owner_id=definition.id, created_at=utcnow(),
        )
    )
    db.commit()
    assert admin_client.delete(f"{DEFS}/{ctype}").status_code == 200
    db.expire_all()
    media = db.query(TeamMedia).filter_by(token="tok_shared_picture_1").one()
    assert media.owner_type == svc.MEDIA_OWNER_TYPE and media.owner_id == copy.id


def test_a_used_checklist_is_409_with_how_many_times(db, admin_client):
    ctype = _published(admin_client)
    _submission(db, ctype)
    _submission(db, ctype, user_id=0, name="Public visitor")
    r = admin_client.delete(f"{DEFS}/{ctype}")
    assert r.status_code == 409
    assert r.json()["detail"] == "This checklist has been filled in 2 times, so it can't be deleted - turn it off instead."
    assert _row(db, ctype) is not None
    listed = {d["checklist_type"]: d for d in admin_client.get(DEFS).json()["definitions"]}
    assert listed[ctype]["can_delete"] is False
    # Turning it off still works.
    assert admin_client.patch(f"{DEFS}/{ctype}", json={"active": False}).status_code == 200


def test_one_submission_reads_1_time_and_demo_submissions_count_too(db, admin_client):
    ctype = _published(admin_client)
    demo = make_user(db, "app_store_review", is_admin=True)
    _submission(db, ctype, user_id=demo.id, name="app_store_review")
    r = admin_client.delete(f"{DEFS}/{ctype}")
    assert r.status_code == 409
    assert "filled in 1 time," in r.json()["detail"]


def test_built_in_checklists_cannot_be_deleted(db, admin_client):
    listed = {d["checklist_type"]: d for d in admin_client.get(DEFS).json()["definitions"]}
    originals = svc.builtin_types()
    assert "store_closing" in originals  # this store's pack's originals
    for ctype in sorted(originals):
        assert listed[ctype]["builtin"] is True and listed[ctype]["can_delete"] is False
        r = admin_client.delete(f"{DEFS}/{ctype}")
        assert r.status_code == 400
        assert "turn it off instead" in r.json()["detail"]
        assert _row(db, ctype) is not None


def test_a_plain_user_gets_403(db, admin_client):
    ctype = _published(admin_client)
    plain = client_for(db, make_user(db, "chef", features=["daily_checklists"]))
    assert plain.delete(f"{DEFS}/{ctype}").status_code == 403
    assert _row(db, ctype) is not None


def test_a_builder_who_is_not_an_admin_gets_403_but_can_turn_it_off(db, admin_client):
    ctype = _published(admin_client)
    builder = client_for(db, make_user(db, "builder1", features=["daily_checklists", "checklist_builder"]))
    r = builder.delete(f"{DEFS}/{ctype}")
    assert r.status_code == 403
    assert r.json()["detail"] == ADMIN_ONLY_DELETE == (
        "Only admins can delete a checklist. You can turn it off instead."
    )
    assert _row(db, ctype) is not None
    assert builder.patch(f"{DEFS}/{ctype}", json={"active": False}).status_code == 200
    assert _row(db, ctype).active is False


def test_the_demo_login_is_refused_even_as_an_admin(db, admin_client):
    ctype = _published(admin_client)
    demo = client_for(db, make_user(db, "app_store_review", is_admin=True))
    assert demo.delete(f"{DEFS}/{ctype}").status_code == 403
    assert _row(db, ctype) is not None


def test_a_second_admin_can_delete(db, admin_client):
    ctype = _published(admin_client)
    other = client_for(db, make_user(db, "manager", is_admin=True))
    assert other.delete(f"{DEFS}/{ctype}").status_code == 200
    assert _row(db, ctype) is None


def test_the_delete_goes_to_the_platform_audit_log(db, admin_client, monkeypatch):
    from app.platform import audit as audit_mod

    rows = []
    monkeypatch.setattr(audit_mod, "audit", lambda action, **kw: rows.append((action, kw)))
    ctype = _published(admin_client, "Audit me")
    assert admin_client.delete(f"{DEFS}/{ctype}").status_code == 200
    assert len(rows) == 1
    action, kw = rows[0]
    assert action == "checklist.deleted" and kw["actor_type"] == "store_user" and kw["target"] == ctype
    assert kw["details"] == {"title": "Audit me", "versions": 1, "public_link_removed": False, "tasks_removed": 0}



# --- The race: a submission arriving while the delete runs -----------------------------


def test_a_submission_that_arrives_during_the_delete_wins(db, admin_client, monkeypatch):
    """Counted 0, then someone submitted before the delete committed: the
    delete counts again under its lock and refuses (409), removing nothing."""
    from app.db import SessionLocal

    ctype = _published(admin_client)
    real = svc._all_submission_count
    calls = []

    def racing(session, checklist_type):
        n = real(session, checklist_type)
        calls.append(n)
        if len(calls) == 1:
            other = SessionLocal()
            try:
                _submission(other, checklist_type)
            finally:
                other.close()
        return n

    monkeypatch.setattr(svc, "_all_submission_count", racing)
    r = admin_client.delete(f"{DEFS}/{ctype}")
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == "This checklist has been filled in 1 time, so it can't be deleted - turn it off instead."
    assert calls == [0, 1]
    definition = _row(db, ctype)
    assert definition is not None
    assert db.query(ChecklistDefinitionVersion).filter_by(definition_id=definition.id).count() == 1
    assert db.query(ChecklistSubmission).filter_by(checklist_type=ctype).count() == 1


def test_a_submission_racing_a_finished_delete_is_refused_not_orphaned(db, admin_client, admin, monkeypatch):
    """The form was checked against the checklist, then an admin deleted it
    before the submission was saved: nothing is stored pointing at it."""
    from app.db import SessionLocal
    from app.models import User
    from app.services import checklist_submission as sub_svc

    ctype = _published(admin_client)
    real = sub_svc._check_media
    deleted = []

    def racing(session, *args, **kwargs):
        if not deleted:
            other = SessionLocal()
            try:
                deleted.append(svc.delete_definition(other, other.get(User, admin.id), ctype))
            finally:
                other.close()
        return real(session, *args, **kwargs)

    monkeypatch.setattr(sub_svc, "_check_media", racing)
    r = admin_client.post(f"/api/checklists/{ctype}/submit", json={"answers": {"i1": "Yes"}})
    assert deleted and deleted[0]["deleted"] is True
    assert r.status_code == 404, r.text
    db.expire_all()
    assert db.query(ChecklistSubmission).filter_by(checklist_type=ctype).count() == 0
    assert _row(db, ctype) is None
