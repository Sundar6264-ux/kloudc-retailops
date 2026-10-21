"""/terms - the Terms of Service draft (pre-production 2026-10-21)."""

import pathlib

from app.routers import legal as legal_router

REPO = pathlib.Path(__file__).resolve().parents[2]


def test_terms_page_is_public_and_plain():
    from fastapi.testclient import TestClient

    from app.main import app

    r = TestClient(app).get("/terms")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    body = r.text
    assert "<title>Terms of Service - KloudC RetailOps</title>" in body
    for must in ("KloudC LLC", "Connecticut", "support@kloudc.com", "Delete my account"):
        assert must in body
    assert "<script" not in body


def test_server_copy_matches_the_wordpress_draft():
    draft = (REPO / "docs" / "legal" / "TERMS_DRAFT.html").read_text(encoding="utf-8")
    assert legal_router.TERMS_FILE.read_text(encoding="utf-8") == draft
    assert "DRAFT" in draft.split("-->")[0], "the lawyer note is an HTML comment, not shown on the page"
    assert "<html" not in draft and "<head" not in draft, "a fragment WordPress can take as is"
