"""Open Checks "Server" filter: /api/orders/employees when the POS can't answer.

A store whose Toast keys are still blank (a newly approved store, the pilot
before the owner fills in Settings > Integrations), or a Toast outage, used to
answer a bare 500 with a stack trace in error.log. It is now a plain 503 the
page already handles (it hides the filter).
"""

import httpx

from app.routers import orders


def test_employees_answers_503_when_the_pos_cannot_be_reached(monkeypatch, admin_client):
    def boom():
        raise httpx.ConnectError("no route to the POS")

    monkeypatch.setattr(orders, "get_employees", boom)
    res = admin_client.get("/api/orders/employees")
    assert res.status_code == 503
    assert "employee list" in res.json()["detail"]


def test_employees_answers_503_when_the_keys_are_blank(monkeypatch, admin_client):
    def refused():
        raise RuntimeError("Toast credentials are not set")

    monkeypatch.setattr(orders, "get_employees", refused)
    assert admin_client.get("/api/orders/employees").status_code == 503


def test_employees_still_lists_when_the_pos_answers(monkeypatch, admin_client):
    monkeypatch.setattr(orders, "get_employees", lambda: [{"guid": "e1", "name": "Sam"}])
    res = admin_client.get("/api/orders/employees")
    assert res.status_code == 200
    assert res.json() == {"employees": [{"guid": "e1", "name": "Sam"}]}
