"""Security review fixes on the store side (docs/SECURITY_REVIEW.md SR-02, SR-04)."""

from app.auth import hash_password
from tests.conftest import client_for


def test_change_password_ends_other_sessions_and_keeps_this_one(db, admin):
    admin.password_hash = hash_password("old pass word")
    db.commit()
    here = client_for(db, admin)
    phone = client_for(db, admin)  # the same account signed in elsewhere
    assert phone.get("/api/auth/me").status_code == 200

    r = here.post("/api/auth/change-password", json={"current_password": "old pass word", "new_password": "new pass word"})
    assert r.status_code == 200, r.text
    assert here.get("/api/auth/me").status_code == 200
    assert phone.get("/api/auth/me").status_code == 401


def test_change_password_guesses_are_rate_limited(db, admin):
    admin.password_hash = hash_password("old pass word")
    db.commit()
    c = client_for(db, admin)
    codes = [
        c.post("/api/auth/change-password", json={"current_password": f"guess {i}", "new_password": "new pass word"}).status_code
        for i in range(6)
    ]
    assert codes[:5] == [401] * 5 and codes[5] == 429


# --- SR-02 API docs off in production ------------------------------------------------------------


def test_api_docs_are_off_in_production(anon_client):
    from app.config import get_settings
    from app.main import api_docs_urls, app
    from app.platform.config import env_is_production

    assert api_docs_urls("production") == {"docs_url": None, "redoc_url": None, "openapi_url": None}
    assert api_docs_urls("staging")["openapi_url"] is None  # anything not dev/test counts as production
    assert api_docs_urls("test")["openapi_url"] == "/openapi.json"
    # The app was built from the process settings (APP_ENV unset = production).
    assert app.openapi_url == api_docs_urls(get_settings().app_env)["openapi_url"]
    if env_is_production(get_settings().app_env):
        for path in ("/openapi.json", "/docs", "/redoc"):
            r = anon_client.get(path)
            assert "swagger" not in r.text.lower() and '"openapi"' not in r.text


# --- SR-10 a vendor name is never PDF markup ---------------------------------------------------


def test_po_pdf_supplier_name_cannot_embed_a_server_file(db, tmp_path):
    import io

    from PIL import Image
    from pypdf import PdfReader

    from app.services import po_export

    secret = tmp_path / "other-store-photo.png"
    Image.new("RGB", (8, 8), (200, 0, 0)).save(secret)
    items = [{"name": "Rice 10 lb", "supplier_item_id": "R1", "qty": 2}]

    def images(pdf: bytes) -> int:
        return sum(len(p.images) for p in PdfReader(io.BytesIO(pdf)).pages)

    plain = po_export.export_simple_po_pdf("Acme Foods", items)
    evil = po_export.export_simple_po_pdf(f'<img src="{secret}" width="40" height="40"/>', items)
    assert images(evil) == images(plain)
    text = "".join(p.extract_text() for p in PdfReader(io.BytesIO(evil)).pages)
    assert "<img" in text  # shown as the text it is
    odd = po_export.export_simple_po_pdf("A & B <Foods>", items)
    assert "A & B <Foods>" in "".join(p.extract_text() for p in PdfReader(io.BytesIO(odd)).pages)
