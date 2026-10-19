"""End-to-end platform flows on PostgreSQL, through the HTTP API (verifier
"flows"): bootstrap a master with the CLI -> two sign-up requests -> the
master approves both at once -> both store databases are provisioned ->
each owner signs in on the one login (Store ID + username + password) ->
Settings > Integrations (Toast saved + Test connection, faked) -> the
store's data sync job runs for that store only, with that store's own
credentials -> the owner adds a staff login -> staff signs in -> the master
suspends the store -> logins and live sessions are refused, the other store
keeps working -> resume.

Nothing here calls Toast: the Toast HTTP client is a recording fake.
"""

import datetime as dt
import json
import threading
import types

import httpx
import pytest
from sqlalchemy import create_engine, select, text

from app.platform import connector_tests, provisioning
from app.platform.connector_tests import TestResult
from app.platform.models import Store
from tests.platform.conftest import _url_for, signup_body, store_login

PW_A = "alpha-owner-pass"
PW_B = "bravo-owner-pass"


def _store_rows(pdb, code):
    pdb.expire_all()
    return pdb.execute(select(Store).where(Store.code == code)).scalar_one()


def _db_scalar(pdb, code, sql):
    store = _store_rows(pdb, code)
    eng = create_engine(_url_for(store.db_name))
    try:
        with eng.connect() as c:
            return c.execute(text(sql)).all()
    finally:
        eng.dispose()


def _signup_two(client):
    ids = {}
    for code, owner, pw, tz in (("alpha", "ann", PW_A, "America/Chicago"), ("bravo", "bob", PW_B, "America/New_York")):
        r = client.post("/api/signup", json=signup_body(store_name=f"{code.title()} Grocery", requested_code=code,
                                                         owner_username=owner, owner_password=pw, timezone=tz,
                                                         contact_email=f"{owner}@example.com"))
        assert r.status_code == 202, r.text
        ids[code.upper()] = r.json()["request_id"]
    return ids


def test_two_approvals_at_once_provision_both_databases(client, master_client, pdb):
    """The master approves two requests back to back: both provisioning
    threads migrate at the same time, and each store gets its own complete
    database."""
    ids = _signup_two(client)
    barrier = threading.Barrier(2)
    real = provisioning.MIGRATOR

    def together(url):
        # Line both migrations up so they really overlap (as two approvals
        # a second apart do on a slow server).
        try:
            barrier.wait(10)
        except threading.BrokenBarrierError:
            pass
        return real(url)

    provisioning.MIGRATOR = together
    try:
        for code, req in ids.items():
            r = master_client.post(f"/api/platform/signups/{req}/approve", json={"pack_id": "test_toast"})
            assert r.status_code == 202, r.text
        results = {code: provisioning.wait_for(code, 180) for code in ids}
    finally:
        provisioning.MIGRATOR = real
    for code, res in results.items():
        assert res and res["status"] == "active", (code, res)
    head = {code: _store_rows(pdb, code).schema_revision for code in ids}
    assert head["ALPHA"] and head["ALPHA"] == head["BRAVO"], head
    assert _db_scalar(pdb, "ALPHA", "SELECT username FROM users") == [("ann",)]
    assert _db_scalar(pdb, "BRAVO", "SELECT username FROM users") == [("bob",)]
    assert store_login(client, "ALPHA", "ann", PW_A).status_code == 200
    assert store_login(client, "BRAVO", "bob", PW_B).status_code == 200
