"""Marketing flyers backend (docs/MARKETING_FLYERS_CONTRACT.md, sections 1-5).

Every test gets its own FLYERS_DIR under tmp_path, and Whapi is always fake:
any httpx call that isn't routed to FakeWhapi fails the test. The sender
runs inline (no thread) with its pauses recorded instead of slept. Nothing
here sends a real WhatsApp message.
"""

import datetime as dt
import io
import os
import pathlib
import sqlite3
import subprocess
import struct
import sys
import time
import uuid
import zlib

import httpx
import pytest
from PIL import Image

from app.config import get_settings
from app.db import Base, SessionLocal
from app.demo_guard import ALLOWLIST, DEMO_READ_ONLY
from app.features import FEATURE_KEYS, FEATURES
from app.models import Flyer, FlyerSend, User, WhatsAppGroup
from app.services import flyer_sender, flyers, notifications, whapi_client
from app.timeutil import to_restaurant_date, utcnow
from tests.conftest import client_for, make_user

# Made-up rules in the shape these tests were written for (six flyer
# categories, two WhatsApp roles): tests/fixtures/packs/suite.json.
pytestmark = pytest.mark.suite_pack


BACKEND = pathlib.Path(__file__).resolve().parents[1]
# The real queue + thread (the `pauses` fixture swaps in an inline stand-in).
REAL_ENQUEUE = flyer_sender.enqueue
API = "/api/flyers"
GROUPS = "/api/checklists/admin/whatsapp-groups"
CHAT_A = "120363011111111111@g.us"
CHAT_B = "120363022222222222@g.us"
CHAT_C = "120363033333333333@g.us"
# 12:00 in the store (EDT) - outside quiet hours.
NOON = dt.datetime(2026, 10, 5, 16, 0, tzinfo=dt.timezone.utc)
# 23:30 in the store - quiet hours.
NIGHT = dt.datetime(2026, 10, 6, 3, 30, tzinfo=dt.timezone.utc)


# --- Fakes + fixtures -----------------------------------------------------------------------


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                str(self.status_code), request=httpx.Request("POST", "https://whapi.test"), response=None
            )


class FakeWhapi:
    """POST /media, POST /messages/{image,document}, GET /messages/list/{chat}.

    plan[chat_id] = list of behaviours for that chat's next sends: "ok",
    "timeout" (no answer, nothing sent), "late" (no answer, but it went
    out), "drop" (connection dropped, nothing sent), "unsent" (200 with
    sent=false, nothing sent), or an HTTP status code."""

    def __init__(self):
        self.uploads = []
        self.sends = []
        self.lists = []
        self.history = {}
        self.plan = {}
        self.upload_status = 200
        self.upload_id = "jpeg-0a1b2c3d-4e5f-6789"
        self.ignore_offset = False
        self._n = 0
        # WhatsApp's clock for new messages: a few seconds after the
        # sender's (patched) "now".
        self.clock = lambda: NOON.timestamp() + 3

    def _deliver(self, kind, payload):
        self._n += 1
        msg = {
            "id": f"msg-{self._n}",
            "from_me": True,
            "type": kind,
            "chat_id": payload["to"],
            "timestamp": int(self.clock()),
            kind: {"caption": payload.get("caption")},
        }
        if kind == "document":
            msg[kind]["file_name"] = payload.get("filename")
        self.history.setdefault(payload["to"], []).append(msg)
        return msg

    def post(self, url, headers=None, json=None, content=None, timeout=None, **kw):
        assert url.startswith(get_settings().whapi_base_url), url
        assert headers["Authorization"] == "Bearer test-token"
        if url.endswith("/media"):
            self.uploads.append({"content_type": headers.get("Content-Type"), "size": len(content)})
            if isinstance(self.upload_status, Exception):
                raise self.upload_status
            if self.upload_status != 200:
                return FakeResponse({"error": {"code": self.upload_status}}, self.upload_status)
            return FakeResponse({"media": [{"id": self.upload_id}]})
        kind = url.rsplit("/", 1)[-1]
        assert url.endswith(("/messages/image", "/messages/document")), url
        self.sends.append(dict(json, kind=kind))
        steps = self.plan.get(json["to"]) or []
        step = steps.pop(0) if steps else "ok"
        if step == "timeout":
            raise httpx.ReadTimeout("slow")
        if step == "late":
            self._deliver(kind, json)
            raise httpx.ReadTimeout("slow")
        if step == "drop":
            raise httpx.ConnectError("refused")
        if step == "unsent":
            return FakeResponse({"sent": False})
        if isinstance(step, int):
            return FakeResponse({"error": {"code": step, "message": "nope"}}, step)
        msg = self._deliver(kind, json)
        return FakeResponse({"sent": True, "message": {"id": msg["id"], "type": kind}})

    def get(self, url, headers=None, params=None, timeout=None, **kw):
        assert "/messages/list/" in url, f"unexpected Whapi GET {url}"
        chat = url.rsplit("/", 1)[-1]
        self.lists.append(chat)
        offset = params.get("offset", 0)
        if self.ignore_offset:
            offset = 0
        newest_first = list(reversed(self.history.get(chat, [])))[offset: offset + params.get("count", 100)]
        return FakeResponse({"messages": newest_first})

    def sent_to(self):
        return [s["to"] for s in self.sends]


@pytest.fixture(autouse=True)
def base(tmp_path, monkeypatch):
    folder = tmp_path / "Store Flyers"
    monkeypatch.setattr(get_settings(), "flyers_dir", str(folder))
    return folder


@pytest.fixture(autouse=True)
def _never_real_whapi(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a real Whapi call was attempted")

    monkeypatch.setattr(whapi_client.httpx, "get", refuse)
    monkeypatch.setattr(whapi_client.httpx, "post", refuse)


@pytest.fixture(autouse=True)
def pauses(monkeypatch):
    """The sender runs inline, its sleeps recorded (not slept), at noon."""
    slept = []
    monkeypatch.setattr(flyer_sender, "sleep", slept.append)
    monkeypatch.setattr(flyer_sender, "enqueue", lambda ids: [flyer_sender.process_send(i) for i in ids])
    monkeypatch.setitem(flyer_sender._pacing, "last_send_done", 0.0)  # per store (platform B)
    monkeypatch.setattr(flyer_sender, "utcnow", lambda: NOON)
    flyer_sender.clear_media_cache()
    yield slept
    flyer_sender.clear_media_cache()


@pytest.fixture(autouse=True)
def pushes(monkeypatch):
    sent = []

    def fake_dispatch(db, alert):
        sent.append(alert)
        return 1

    monkeypatch.setattr(flyer_sender, "dispatch_alert", fake_dispatch)
    return sent


@pytest.fixture
def whapi(monkeypatch):
    fake = FakeWhapi()
    monkeypatch.setattr(get_settings(), "whapi_api_token", "test-token")
    monkeypatch.setattr(whapi_client.httpx, "post", fake.post)
    monkeypatch.setattr(whapi_client.httpx, "get", fake.get)
    return fake


@pytest.fixture
def marketing(admin_client):
    """Groups: "Customers Vernon" (A) and "Customers Manchester" (B) marked
    for flyers, "Deli staff" (C) not; the built-ins untouched."""
    for label, chat, flag in (("Customers Vernon", CHAT_A, True), ("Customers Manchester", CHAT_B, True),
                              ("Deli staff", CHAT_C, False)):
        r = admin_client.post(GROUPS, json={"chat_id": chat, "label": label})
        assert r.status_code == 200, r.text
        if flag:
            r = admin_client.patch(f"{GROUPS}/{r.json()['key']}", json={"marketing": True})
            assert r.status_code == 200 and r.json()["marketing"] is True, r.text
    return {"a": "customers_vernon", "b": "customers_manchester", "c": "deli_staff"}


# --- Files ------------------------------------------------------------------------------------


def jpeg(w=300, h=200, orientation=None, color=(200, 30, 30), quality=90):
    img = Image.new("RGB", (w, h), color)
    exif = Image.Exif()
    exif[0x010E] = "secret description"
    exif[0x010F] = "PhoneMaker"
    if orientation:
        exif[0x0112] = orientation
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif.tobytes(), quality=quality)
    return buf.getvalue()


def png_rgba(w=240, h=160):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    img.paste((0, 128, 0, 255), (0, 0, w // 2, h))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def webp(w=200, h=200):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (20, 20, 220)).save(buf, "WEBP", quality=80)
    return buf.getvalue()


def heic(w=320, h=240):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 120, 200)).save(buf, format="HEIF", quality=80)
    return buf.getvalue()


def pdf(pages=1, text="Diwali Sale"):
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792))
    for n in range(pages):
        c.setFont("Helvetica", 40)
        c.drawString(100, 600, f"{text} {n + 1}")
        c.showPage()
    c.save()
    return buf.getvalue()


def upload(client, data, name="flyer.jpg", title="Diwali Garlands", category="flowers", caption=None,
           content_type="image/jpeg"):
    form = {"title": title, "category": category}
    if caption is not None:
        form["caption"] = caption
    return client.post(API, data=form, files={"file": (name, data, content_type)})


def made(client, data=None, **kw):
    r = upload(client, data if data is not None else jpeg(), **kw)
    assert r.status_code == 201, r.text
    return r.json()


def send(client, flyer_id, keys, key=None):
    return client.post(f"{API}/{flyer_id}/send", json={"group_keys": keys, "idempotency_key": key or str(uuid.uuid4())})


def stored(base, flyer_json, db):
    row = db.get(Flyer, flyer_json["id"])
    db.refresh(row)
    return base / row.file_rel_path, row


# --- Upload ------------------------------------------------------------------------------------


def test_upload_jpeg_is_normalized_and_stored_by_category(admin_client, db, base):
    body = made(admin_client, jpeg(300, 200, orientation=6), name="IMG_0001.JPG", caption="  *Diwali* offers\r\nToday  ")
    assert body["kind"] == "image" and body["mime"] == "image/jpeg"
    # EXIF orientation 6 (rotated) is applied: 300x200 becomes 200x300.
    assert (body["width"], body["height"]) == (200, 300)
    assert body["category"] == "flowers" and body["category_label"] == "Flowers"
    assert body["caption"] == "*Diwali* offers\nToday"
    assert body["file_name"] == "Diwali Garlands.jpg"
    assert body["file_url"] == f"/api/flyers/{body['id']}/file"
    assert body["thumb_url"] == f"/api/flyers/{body['id']}/thumb"
    assert body["send_counts"] == {"sent": 0, "failed": 0, "pending": 0} and body["last_sent_at"] is None
    assert body["created_by_name"] == "boss" and body["created_at"].endswith("+00:00")
    path, row = stored(base, body, db)
    day = to_restaurant_date(row.created_at).isoformat()
    assert row.file_rel_path == f"Flowers/{day} Diwali Garlands ({row.id}).jpg"
    assert path.is_file() and path.stat().st_size == row.size_bytes == body["size_bytes"]
    assert row.original_filename == "IMG_0001.JPG"
    import hashlib

    assert row.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    with Image.open(path) as img:
        assert img.format == "JPEG" and img.size == (200, 300)
        # Metadata stripped: no EXIF at all (camera, description, GPS...).
        assert not dict(img.getexif()) and "exif" not in img.info
    # All six category folders exist (first use), in Finder-friendly names.
    assert {p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")} == {
        "Groceries", "Kitchen", "Flowers", "Halal Meat", "Store", "Others"}
    # Nothing temporary left behind.
    assert not [p for p in (base / "Flowers").iterdir() if p.name.startswith(".")]


@pytest.mark.parametrize("maker, name, ctype", [
    (png_rgba, "art.png", "image/png"),
    (webp, "art.webp", "image/webp"),
    (heic, "IMG_1234.HEIC", "image/heic"),
])
def test_upload_png_webp_heic_become_jpeg(admin_client, db, base, maker, name, ctype):
    data = maker()
    body = made(admin_client, data, name=name, content_type=ctype, category="groceries")
    path, row = stored(base, body, db)
    assert row.mime == "image/jpeg" and path.suffix == ".jpg" and path.parent.name == "Groceries"
    with Image.open(path) as img:
        assert img.format == "JPEG" and img.mode == "RGB"
        if maker is png_rgba:
            # Transparency is flattened onto white, not black.
            assert img.getpixel((img.width - 5, 5))[0] > 240


def test_validated_by_content_not_by_name_or_type(admin_client, db, base):
    # A PNG named .pdf and declared as a PDF is still stored as an image.
    body = made(admin_client, png_rgba(), name="sneaky.pdf", content_type="application/pdf")
    assert body["kind"] == "image"
    # A PDF declared as a JPEG is still a PDF.
    data = pdf()
    body = made(admin_client, data, name="photo.jpg", content_type="image/jpeg", category="halal_meat")
    path, row = stored(base, body, db)
    assert body["kind"] == "pdf" and row.mime == "application/pdf" and path.suffix == ".pdf"
    assert body["width"] is None and body["height"] is None
    # Stored exactly as uploaded.
    assert path.read_bytes() == data


@pytest.mark.parametrize("data, name, needle", [
    (b"\xff\xd8\xff\xe0" + b"\x00garbage" * 200, "broken.jpg", "damaged"),
    (b"%PDF-1.7\n" + b"\x00not really a pdf" * 100, "broken.pdf", "damaged"),
    (b"hello, I am a text file", "notes.txt", "isn't supported"),
    (b"GIF89a" + b"\x00" * 50, "anim.gif", "isn't supported"),
    (b"", "empty.jpg", "empty"),
])
def test_corrupt_or_unsupported_files_are_400(admin_client, base, data, name, needle):
    r = upload(admin_client, data, name=name)
    assert r.status_code == 400, r.text
    assert needle in r.json()["detail"]
    # Nothing stored.
    assert not list(base.rglob("*.jpg")) and not list(base.rglob("*.pdf"))


def test_real_gif_is_unsupported(admin_client):
    buf = io.BytesIO()
    Image.new("P", (10, 10)).save(buf, "GIF")
    r = upload(admin_client, buf.getvalue(), name="a.gif")
    assert r.status_code == 400 and "isn't supported" in r.json()["detail"]


def test_decompression_bomb_is_refused_from_the_header(admin_client):
    # 10000 x 6000 = 60 MP (> MAX_PIXELS), but a tiny file.
    buf = io.BytesIO()
    Image.new("1", (10000, 6000)).save(buf, "PNG")
    assert len(buf.getvalue()) < 200_000
    r = upload(admin_client, buf.getvalue(), name="bomb.png")
    assert r.status_code == 400 and "too many pixels" in r.json()["detail"]


def test_big_image_is_downscaled_and_kept_under_the_size_target(admin_client, db, base, monkeypatch):
    # Noise compresses badly: with a small target the quality / size steps kick in.
    monkeypatch.setattr(flyers, "TARGET_MAX_BYTES", 120_000)
    noise = Image.frombytes("RGB", (900, 700), os.urandom(900 * 700 * 3))
    buf = io.BytesIO()
    noise.save(buf, "PNG")
    body = made(admin_client, buf.getvalue(), name="noise.png")
    path, row = stored(base, body, db)
    assert row.size_bytes <= 120_000
    # And a big photo is shrunk to MAX_SIDE on its longest side.
    big = jpeg(4000, 3000, color=(90, 160, 40))
    body = made(admin_client, big, name="big.jpg", title="Big one")
    assert max(body["width"], body["height"]) == flyers.MAX_SIDE == 2560


P3_PROFILE = pathlib.Path("/System/Library/ColorSync/Profiles/Display P3.icc")


@pytest.mark.skipif(not P3_PROFILE.exists(), reason="macOS Display P3 profile not here")
def test_display_p3_photo_is_converted_to_srgb(admin_client, db, base):
    # An iPhone-style Display P3 photo: its colours are converted to sRGB,
    # not just stripped of the profile (which would wash them out).
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 100, 50)).save(buf, "PNG", icc_profile=P3_PROFILE.read_bytes())
    body = made(admin_client, buf.getvalue(), name="p3.png")
    path, _ = stored(base, body, db)
    with Image.open(path) as img:
        assert "icc_profile" not in img.info
        r, g, b = img.getpixel((32, 32))
    # P3 (200, 100, 50) is about sRGB (215, 93, 31).
    assert abs(r - 215) <= 3 and abs(g - 93) <= 3 and abs(b - 31) <= 3, (r, g, b)


def test_too_big_files_are_400(admin_client, base):
    # Over the 25 MB image limit (but under the whole-request cap): counted while reading.
    r = upload(admin_client, b"\xff\xd8\xff" + b"\x00" * (25 * 1024 * 1024), name="huge.jpg")
    assert r.status_code == 400 and "too big" in r.json()["detail"]
    # A PDF over 20 MB.
    r = upload(admin_client, b"%PDF-1.4\n" + os.urandom(21 * 1024 * 1024), name="huge.pdf", content_type="application/pdf")
    assert r.status_code == 400 and "20 MB" in r.json()["detail"]
    assert not list(base.rglob("*.pdf"))


def test_request_size_is_checked_before_the_body_is_read():
    class Req:
        def __init__(self, headers):
            self.headers = headers

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as e:
        flyers.check_request_size(Req({"content-length": str(flyers.MAX_REQUEST_BYTES + 1)}))
    assert e.value.status_code == 400 and "too big" in e.value.detail
    with pytest.raises(HTTPException) as e:
        flyers.check_request_size(Req({}))
    assert e.value.status_code == 411
    with pytest.raises(HTTPException) as e:
        flyers.check_request_size(Req({"content-length": "lots"}))
    assert e.value.status_code == 400
    flyers.check_request_size(Req({"content-length": str(flyers.MAX_REQUEST_BYTES)}))


def test_upload_is_parsed_and_processed_off_the_event_loop(admin_client, monkeypatch):
    """python-multipart can take many seconds on a pathological body; that
    must never run on the server's event loop (it would freeze every other
    request)."""
    import asyncio

    seen = []
    real = flyers.parse_upload

    def spy(content_type, body):
        try:
            asyncio.get_running_loop()
            seen.append("event loop")
        except RuntimeError:
            seen.append("worker thread")
        return real(content_type, body)

    monkeypatch.setattr(flyers, "parse_upload", spy)
    made(admin_client)
    assert seen == ["worker thread"]


def test_parse_upload():
    boundary = "b0undary"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\nHello\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.jpg\"\r\n"
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode() + b"\xff\xd8data" + f"\r\n--{boundary}--\r\n".encode()
    parts = flyers.parse_upload(f"multipart/form-data; boundary={boundary}", body)
    assert parts.data == b"\xff\xd8data" and parts.filename == "a.jpg" and parts.fields == {"title": "Hello"}
    assert flyers.parse_upload("application/json", b"{}") == flyers.UploadParts(None, None, {})
    with pytest.raises(flyers.FlyerError):
        flyers.parse_upload("multipart/form-data", body)  # no boundary


def test_huge_request_is_refused_without_reading_it(admin_client, monkeypatch):
    read = []
    monkeypatch.setattr(flyers, "read_limited", lambda *a, **k: read.append(1) or b"")
    big = b"\x00" * (flyers.MAX_REQUEST_BYTES + 10)
    r = admin_client.post(API, data={"title": "x", "category": "others"}, files={"file": ("a.jpg", big, "image/jpeg")})
    assert r.status_code == 400 and "too big" in r.json()["detail"] and read == []


@pytest.mark.parametrize("form, needle", [
    ({"title": "   ", "category": "flowers"}, "title"),
    ({"category": "flowers"}, "title"),
    ({"title": "x" * 121, "category": "flowers"}, "120"),
    ({"title": "Ok", "category": "bakery"}, "Choose a category"),
    ({"title": "Ok"}, "Choose a category"),
    ({"title": "Ok", "category": "flowers", "caption": "y" * 1001}, "1,000"),
])
def test_bad_fields_are_400(admin_client, base, form, needle):
    r = admin_client.post(API, data=form, files={"file": ("a.jpg", jpeg(), "image/jpeg")})
    assert r.status_code == 400 and needle in r.json()["detail"], r.text
    assert not list(base.rglob("*.jpg"))


def test_missing_file_or_empty_body_is_400(admin_client):
    r = admin_client.post(API, data={"title": "No file", "category": "others"})
    assert r.status_code == 400 and "Choose an image or a PDF" in r.json()["detail"]
    r = admin_client.post(API, content=b"", headers={"content-type": "multipart/form-data; boundary=x"})
    assert r.status_code == 400
    r = admin_client.post(API, json={"title": "x"})
    assert r.status_code == 400
    # Two files: refused by the multipart parser.
    r = admin_client.post(API, data={"title": "x", "category": "others"},
                          files=[("file", ("a.jpg", jpeg(), "image/jpeg")), ("file", ("b.jpg", jpeg(), "image/jpeg"))])
    assert r.status_code == 400


def test_password_protected_pdf_is_refused(admin_client):
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(pdf())).pages:
        writer.add_page(page)
    writer.encrypt(user_password="secret", owner_password="owner")
    buf = io.BytesIO()
    writer.write(buf)
    r = upload(admin_client, buf.getvalue(), name="locked.pdf", content_type="application/pdf")
    assert r.status_code == 400 and "password" in r.json()["detail"]


# --- File names -------------------------------------------------------------------------------


@pytest.mark.parametrize("title, part", [
    ("Diwali / Garlands: *Special*", "Diwali Garlands Special"),
    ("../../etc/passwd", "etc passwd"),
    ("...hidden", "hidden"),
    ("...", "Flyer"),
    ("Eid\u202emubarak\x07 sale", "Eidmubarak sale"),
    ("मिठाई Sale 🪔", "मिठाई Sale 🪔"),
])
def test_safe_file_title(title, part):
    assert flyers.safe_file_title(title) == part


def test_long_titles_are_cut_for_the_file_name_only(admin_client, db, base):
    title = "Weekend special " * 7  # 112 characters
    body = made(admin_client, title=title.strip())
    assert body["title"] == title.strip()
    path, row = stored(base, body, db)
    part = path.name.split(" ", 1)[1].rsplit(" (", 1)[0]
    assert len(part) <= 80
    # Multi-byte titles are cut by bytes too (APFS allows 255 bytes per name).
    assert len(flyers.safe_file_title("ई" * 120).encode("utf-8")) <= flyers.FILE_TITLE_BYTES


def test_client_file_name_is_never_used_for_the_path(admin_client, db, base):
    body = made(admin_client, name="../../../../tmp/evil.jpg", title="Safe")
    path, row = stored(base, body, db)
    assert path.parent == base / "Flowers" and "evil" not in path.name
    assert row.original_filename == "evil.jpg"


def test_an_existing_file_is_never_overwritten(admin_client, db, base):
    # A file already sitting where the next flyer would go (e.g. left from a restored database).
    day = to_restaurant_date(utcnow()).isoformat()
    (base / "Flowers").mkdir(parents=True)
    squatter = base / "Flowers" / f"{day} Diwali Garlands (1).jpg"
    squatter.write_bytes(b"keep me")
    body = made(admin_client)
    path, row = stored(base, body, db)
    assert row.id == 1 and path.name == f"{day} Diwali Garlands (1-2).jpg"
    assert squatter.read_bytes() == b"keep me"


# --- Rename / move / delete ----------------------------------------------------------------------


def test_rename_and_category_move_keep_date_and_id(admin_client, db, base):
    body = made(admin_client, title="Diwali Garlands")
    old_path, row = stored(base, body, db)
    day = old_path.name.split(" ", 1)[0]
    r = admin_client.patch(f"{API}/{body['id']}", json={"title": "Diwali Marigolds", "caption": "New *offer*"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["title"] == "Diwali Marigolds" and out["caption"] == "New *offer*" and out["sends"] == []
    path, row = stored(base, body, db)
    assert row.file_rel_path == f"Flowers/{day} Diwali Marigolds ({row.id}).jpg"
    assert path.is_file() and not old_path.exists()
    assert row.updated_at is not None

    r = admin_client.patch(f"{API}/{body['id']}", json={"category": "store"})
    assert r.status_code == 200 and r.json()["category_label"] == "Store (Generic)"
    moved, row = stored(base, body, db)
    assert row.file_rel_path == f"Store/{day} Diwali Marigolds ({row.id}).jpg"
    assert moved.is_file() and not path.exists()
    # A case-only change on a case-insensitive disk just changes the case.
    r = admin_client.patch(f"{API}/{body['id']}", json={"title": "diwali marigolds"})
    assert r.status_code == 200
    final, row = stored(base, body, db)
    assert final.name == f"{day} diwali marigolds ({row.id}).jpg" and final.is_file()
    assert len(list((base / "Store").iterdir())) == 1
    # Caption cleared; nothing else moves.
    r = admin_client.patch(f"{API}/{body['id']}", json={"caption": "  "})
    assert r.json()["caption"] is None
    assert stored(base, body, db)[0] == final


def test_patch_validation(admin_client):
    body = made(admin_client)
    for payload, code in (({"title": ""}, 400), ({"category": "x"}, 400), ({"caption": 5}, 400),
                          ({"title": None}, 400)):
        assert admin_client.patch(f"{API}/{body['id']}", json=payload).status_code == code, payload
    assert admin_client.patch(f"{API}/9999", json={"title": "x"}).status_code == 404
    assert admin_client.patch(f"{API}/{body['id']}", json=["x"]).status_code == 422


def test_rename_with_the_file_missing_still_saves(admin_client, db, base):
    body = made(admin_client)
    path, _ = stored(base, body, db)
    path.unlink()  # someone deleted it in Finder
    r = admin_client.patch(f"{API}/{body['id']}", json={"title": "Renamed", "category": "others"})
    assert r.status_code == 200 and r.json()["title"] == "Renamed"
    assert admin_client.get(f"{API}/{body['id']}/file").status_code == 404


def test_file_renamed_or_moved_in_finder_is_still_found(admin_client, db, base):
    body = made(admin_client, title="Roses")
    path, row = stored(base, body, db)
    # Someone renames it by hand, keeping the "(id)" part...
    renamed = path.with_name(f"Valentine roses ({row.id}).jpg")
    path.rename(renamed)
    r = admin_client.get(f"{API}/{body['id']}/file")
    assert r.status_code == 200 and r.content == renamed.read_bytes()
    # ...then drags it into another category folder.
    moved = base / "Others" / renamed.name
    renamed.rename(moved)
    assert admin_client.get(f"{API}/{body['id']}/thumb").status_code == 200
    # A caption edit remembers where it is now.
    assert admin_client.patch(f"{API}/{body['id']}", json={"caption": "Hi"}).status_code == 200
    _, row = stored(base, body, db)
    assert row.file_rel_path == f"Others/{moved.name}"
    # Two files claiming the same id: neither is guessed.
    (base / "Flowers" / f"copy ({row.id}).jpg").write_bytes(b"x")
    moved.rename(base / "Groceries" / moved.name)
    assert admin_client.get(f"{API}/{body['id']}/file").status_code == 404


def test_photo_with_pdf_text_in_its_metadata_is_still_a_photo(admin_client):
    img = Image.new("RGB", (50, 50), (1, 2, 3))
    buf = io.BytesIO()
    img.save(buf, "JPEG", comment=b"%PDF-1.4 not a pdf")
    assert b"%PDF-" in buf.getvalue()[:1024]
    assert made(admin_client, buf.getvalue())["kind"] == "image"


def test_16_bit_greyscale_png_is_accepted(admin_client):
    img = Image.new("I;16", (100, 50))
    img.putpixel((10, 10), 65535)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    body = made(admin_client, buf.getvalue(), name="grey.png")
    assert (body["width"], body["height"]) == (100, 50)


def test_soft_delete_moves_the_file_to_deleted(admin_client, db, base):
    body = made(admin_client, category="halal_meat", title="Eid lamb")
    path, row = stored(base, body, db)
    r = admin_client.delete(f"{API}/{body['id']}")
    assert r.status_code == 204 and r.content == b""
    gone, row = stored(base, body, db)
    assert row.deleted_at is not None and not path.exists()
    assert gone == base / "_Deleted" / "Halal Meat" / path.name and gone.is_file()
    assert row.file_rel_path == f"_Deleted/Halal Meat/{path.name}"
    for url in ("", "/file", "/thumb"):
        assert admin_client.get(f"{API}/{body['id']}{url}").status_code == 404
    assert admin_client.delete(f"{API}/{body['id']}").status_code == 404
    assert admin_client.get(API).json()["items"] == []
    # The row is kept (history), never removed.
    assert db.get(Flyer, body["id"]) is not None


# --- List / detail / files -----------------------------------------------------------------------


def test_list_paging_search_and_counts(admin_client):
    ids = []
    for title, cat, caption in (("Mango festival", "groceries", None), ("Biryani Friday", "kitchen", "Hot *biryani*"),
                                ("Roses", "flowers", None), ("Eid lamb", "halal_meat", "Fresh goat and lamb"),
                                ("Grand opening", "store", None)):
        ids.append(made(admin_client, title=title, category=cat, caption=caption)["id"])
    page = admin_client.get(API, params={"limit": 2}).json()
    assert [f["id"] for f in page["items"]] == [ids[4], ids[3]]
    assert page["next_before_id"] == ids[3]
    assert page["counts"] == {"all": 5, "groceries": 1, "kitchen": 1, "flowers": 1, "halal_meat": 1,
                              "store": 1, "others": 0}
    page2 = admin_client.get(API, params={"limit": 2, "before_id": page["next_before_id"]}).json()
    assert [f["id"] for f in page2["items"]] == [ids[2], ids[1]]
    page3 = admin_client.get(API, params={"limit": 2, "before_id": page2["next_before_id"]}).json()
    assert [f["id"] for f in page3["items"]] == [ids[0]] and page3["next_before_id"] is None

    # Search looks at the title and the message; counts follow the search.
    found = admin_client.get(API, params={"q": "LAMB"}).json()
    assert [f["id"] for f in found["items"]] == [ids[3]] and found["counts"]["all"] == 1
    found = admin_client.get(API, params={"q": "biryani"}).json()
    assert [f["title"] for f in found["items"]] == ["Biryani Friday"]
    # LIKE wildcards are plain text.
    assert admin_client.get(API, params={"q": "%"}).json()["items"] == []
    # Category filter; counts ignore it.
    cat = admin_client.get(API, params={"category": "flowers"}).json()
    assert [f["title"] for f in cat["items"]] == ["Roses"] and cat["counts"]["all"] == 5
    assert admin_client.get(API, params={"category": "bakery"}).status_code == 400
    assert admin_client.get(API, params={"limit": 0}).status_code == 422
    assert admin_client.get(API, params={"limit": 101}).status_code == 422


def test_detail_file_and_thumb(admin_client, db, base):
    body = made(admin_client, jpeg(1200, 800))
    detail = admin_client.get(f"{API}/{body['id']}").json()
    assert detail["sends"] == [] and detail["id"] == body["id"]
    r = admin_client.get(f"{API}/{body['id']}/file")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["content-disposition"].startswith("inline") and "Diwali%20Garlands.jpg" in r.headers[
        "content-disposition"] or 'filename="Diwali Garlands.jpg"' in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"
    path, row = stored(base, body, db)
    assert r.content == path.read_bytes()
    t = admin_client.get(f"{API}/{body['id']}/thumb")
    assert t.status_code == 200 and t.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(t.content)) as img:
        assert max(img.size) <= 480 and img.format == "JPEG"
    # Cached in the hidden .thumbs folder.
    assert len(list((base / ".thumbs").glob(f"{row.id}-*.jpg"))) == 1
    assert admin_client.get(f"{API}/12345").status_code == 404
    assert admin_client.get(f"{API}/12345/file").status_code == 404


def test_pdf_file_and_thumbnail(admin_client, db, base, monkeypatch):
    body = made(admin_client, pdf(pages=2), name="menu.pdf", content_type="application/pdf", title="Menu")
    r = admin_client.get(f"{API}/{body['id']}/file")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline")

    # macOS can't render it: the generic PDF icon (PNG), and no retry for a while.
    calls = []
    monkeypatch.setattr(flyers, "_render_pdf_page", lambda pdf_path, out: calls.append(pdf_path) or None)
    t = admin_client.get(f"{API}/{body['id']}/thumb")
    assert t.status_code == 200 and t.headers["content-type"] == "image/png"
    assert admin_client.get(f"{API}/{body['id']}/thumb").headers["content-type"] == "image/png"
    assert len(calls) == 1

    # Rendered: a JPEG of at most 480 px, cached.
    other = made(admin_client, pdf(text="Other"), name="o.pdf", content_type="application/pdf", title="Other")

    def render(pdf_path, out):
        page = out / "page.png"
        Image.new("RGBA", (612, 792), (0, 0, 0, 0)).save(page)
        return page

    monkeypatch.setattr(flyers, "_render_pdf_page", render)
    t = admin_client.get(f"{API}/{other['id']}/thumb")
    assert t.status_code == 200 and t.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(t.content)) as img:
        assert max(img.size) <= 480
        # Transparent page background becomes white.
        assert img.getpixel((5, 5))[0] > 240


@pytest.mark.skipif(sys.platform != "darwin" or not os.path.exists("/usr/bin/sips"), reason="macOS sips only")
def test_pdf_thumbnail_with_the_real_sips(admin_client, db, base):
    body = made(admin_client, pdf(), name="real.pdf", content_type="application/pdf", title="Real")
    t = admin_client.get(f"{API}/{body['id']}/thumb")
    assert t.status_code == 200
    with Image.open(io.BytesIO(t.content)) as img:
        assert max(img.size) <= 480


def test_heic_falls_back_to_sips_when_pillow_cant_read_it(monkeypatch):
    data = heic()
    real_open = Image.open

    def no_heif(fp, *a, **k):
        head = fp.getvalue()[:12] if isinstance(fp, io.BytesIO) else b""
        if head[4:8] == b"ftyp":
            raise Image.UnidentifiedImageError("no HEIF plugin")
        return real_open(fp, *a, **k)

    monkeypatch.setattr(flyers.Image, "open", no_heif)
    converted = jpeg(320, 240)
    monkeypatch.setattr(flyers, "_sips_to_jpeg", lambda d: converted)
    out = flyers.process_upload(data)
    assert out.mime == "image/jpeg" and (out.width, out.height) == (320, 240)
    monkeypatch.setattr(flyers, "_sips_to_jpeg", lambda d: None)
    with pytest.raises(flyers.FlyerError) as e:
        flyers.process_upload(data)
    assert e.value.status_code == 400


# --- Meta + groups -------------------------------------------------------------------------------


def test_meta_lists_only_active_marketing_groups(admin_client, marketing, monkeypatch):
    # Turned off: never offered, even though it's marked for flyers.
    r = admin_client.post(GROUPS, json={"chat_id": "120363044444444444@g.us", "label": "Old customers", "marketing": True})
    assert r.status_code == 200 and r.json()["marketing"] is True
    admin_client.patch(f"{GROUPS}/old_customers", json={"active": False})
    monkeypatch.setattr(get_settings(), "whapi_api_token", "")
    meta = admin_client.get(f"{API}/meta").json()
    assert meta["groups"] == [{"key": "customers_manchester", "label": "Customers Manchester"},
                              {"key": "customers_vernon", "label": "Customers Vernon"}]
    assert meta["categories"] == [
        {"key": "groceries", "label": "Groceries"}, {"key": "kitchen", "label": "Kitchen"},
        {"key": "flowers", "label": "Flowers"}, {"key": "halal_meat", "label": "Halal Meat"},
        {"key": "store", "label": "Store (Generic)"}, {"key": "others", "label": "Others"}]
    assert meta["whatsapp_connected"] is False
    assert (meta["max_image_mb"], meta["max_pdf_mb"], meta["max_groups_per_send"]) == (25, 20, 25)
    assert meta["accept"] == ["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "application/pdf"]
    # No chat id ever leaves the server.
    text = admin_client.get(f"{API}/meta").text
    assert CHAT_A not in text and "@g.us" not in text
    monkeypatch.setattr(get_settings(), "whapi_api_token", "test-token")
    assert admin_client.get(f"{API}/meta").json()["whatsapp_connected"] is True


def test_builtin_staff_groups_start_unmarked_but_can_be_ticked(admin_client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "whapi_spice_town_checklist_group_id", "120363099999999991@g.us")
    items = {g["key"]: g for g in admin_client.get(GROUPS).json()["items"]}
    assert items["store"]["marketing"] is False and items["kitchen"]["marketing"] is False
    assert admin_client.get(f"{API}/meta").json()["groups"] == []
    r = admin_client.patch(f"{GROUPS}/store", json={"marketing": True})
    assert r.status_code == 200 and r.json()["marketing"] is True
    assert admin_client.get(f"{API}/meta").json()["groups"] == [{"key": "store", "label": "Store staff group"}]
    # The kitchen built-in has no chat id (blank env): ticking it offers nothing to send to.
    admin_client.patch(f"{GROUPS}/kitchen", json={"marketing": True})
    assert [g["key"] for g in admin_client.get(f"{API}/meta").json()["groups"]] == ["store"]
    assert admin_client.patch(f"{GROUPS}/store", json={"marketing": "yes"}).status_code == 400
    assert admin_client.post(GROUPS, json={"chat_id": CHAT_C, "label": "X", "marketing": 1}).status_code == 400


# --- Sending -------------------------------------------------------------------------------------


def test_send_happy_path(admin_client, db, whapi, marketing, pauses, pushes):
    body = made(admin_client, caption="*Diwali* garlands - 20% off today")
    r = send(admin_client, body["id"], [marketing["a"], marketing["b"]])
    assert r.status_code == 202, r.text
    out = r.json()
    assert set(out) == {"batch_id", "sends"} and len(out["sends"]) == 2
    first = out["sends"][0]
    assert set(first) == {"id", "batch_id", "channel", "target_key", "target_label", "status", "error",
                          "requested_by_name", "requested_at", "sent_at"}
    assert first["channel"] == "whatsapp" and first["status"] == "queued" and first["requested_by_name"] == "boss"
    assert CHAT_A not in r.text
    # Uploaded to Whapi once, then sent to each group by MediaID, one at a time.
    assert whapi.uploads == [{"content_type": "image/jpeg", "size": body["size_bytes"]}]
    assert whapi.sent_to() == [CHAT_A, CHAT_B]
    for s in whapi.sends:
        assert s == {"to": s["to"], "media": whapi.upload_id, "caption": "*Diwali* garlands - 20% off today",
                     "kind": "image"}
    # A 3-6 s pause between the two groups.
    assert len(pauses) == 1 and 2.9 <= pauses[0] <= 6
    detail = admin_client.get(f"{API}/{body['id']}").json()
    assert [s["status"] for s in detail["sends"]] == ["sent", "sent"]
    assert [s["target_label"] for s in detail["sends"]] == ["Customers Manchester", "Customers Vernon"]
    assert detail["send_counts"] == {"sent": 2, "failed": 0, "pending": 0}
    assert detail["last_sent_at"] == NOON.isoformat()
    rows = db.query(FlyerSend).order_by(FlyerSend.id).all()
    assert [r.provider_message_id for r in rows] == ["msg-1", "msg-2"]
    assert all(r.attempts == 1 and r.target_ref in (CHAT_A, CHAT_B) for r in rows)
    # One push to the sender when the batch is done.
    assert len(pushes) == 1
    push = pushes[0]
    assert push.key == "flyer_sent" and push.title == "Flyer sent to 2 groups" and push.body == "Diwali Garlands"
    assert push.user_ids == [admin_client.user.id] and push.data["flyer_id"] == body["id"]
    # The list shows the counts too.
    listed = admin_client.get(API).json()["items"][0]
    assert listed["send_counts"] == {"sent": 2, "failed": 0, "pending": 0} and "sends" not in listed


def test_pdf_goes_as_a_document_with_its_name(admin_client, whapi, marketing):
    body = made(admin_client, pdf(), name="x.pdf", content_type="application/pdf", title="Weekend Menu")
    assert send(admin_client, body["id"], [marketing["a"]]).status_code == 202
    assert whapi.uploads[0]["content_type"] == "application/pdf"
    assert whapi.sends == [{"to": CHAT_A, "media": whapi.upload_id, "filename": "Weekend Menu.pdf", "kind": "document"}]


def test_partial_failure_then_retry(admin_client, db, whapi, marketing, pushes):
    body = made(admin_client)
    whapi.plan[CHAT_B] = [403]
    out = send(admin_client, body["id"], [marketing["a"], marketing["b"]]).json()
    detail = admin_client.get(f"{API}/{body['id']}").json()
    by_key = {s["target_key"]: s for s in detail["sends"]}
    assert by_key[marketing["a"]]["status"] == "sent"
    failed = by_key[marketing["b"]]
    assert failed["status"] == "failed" and "403" in failed["error"] and failed["sent_at"] is None
    assert detail["send_counts"] == {"sent": 1, "failed": 1, "pending": 0}
    assert [p.title for p in pushes] == ["Flyer: 1 sent, 1 failed"]

    # Only a failed send can be retried.
    sent_id = by_key[marketing["a"]]["id"]
    assert admin_client.post(f"{API}/{body['id']}/sends/{sent_id}/retry").status_code == 409
    r = admin_client.post(f"{API}/{body['id']}/sends/{failed['id']}/retry")
    assert r.status_code == 202 and r.json()["id"] == failed["id"] and r.json()["batch_id"] == out["batch_id"]
    row = db.get(FlyerSend, failed["id"])
    db.refresh(row)
    assert row.status == "sent" and row.attempts == 2 and row.error is None
    assert whapi.sent_to() == [CHAT_A, CHAT_B, CHAT_B]
    # The batch is done again: a new push with the new counts.
    assert [p.title for p in pushes] == ["Flyer: 1 sent, 1 failed", "Flyer sent to 2 groups"]
    assert pushes[0].dedupe_keys != pushes[1].dedupe_keys
    # Unknown send, or another flyer's send: 404.
    other = made(admin_client, title="Other")
    assert admin_client.post(f"{API}/{other['id']}/sends/{failed['id']}/retry").status_code == 404
    assert admin_client.post(f"{API}/{body['id']}/sends/999/retry").status_code == 404


def test_retry_refused_when_the_group_is_no_longer_for_flyers(admin_client, whapi, marketing, monkeypatch):
    body = made(admin_client)
    whapi.plan[CHAT_A] = [500, "ok"]
    out = send(admin_client, body["id"], [marketing["a"]]).json()
    send_id = out["sends"][0]["id"]
    admin_client.patch(f"{GROUPS}/{marketing['a']}", json={"marketing": False})
    r = admin_client.post(f"{API}/{body['id']}/sends/{send_id}/retry")
    assert r.status_code == 400 and "Use for flyers" in r.json()["detail"]
    admin_client.patch(f"{GROUPS}/{marketing['a']}", json={"marketing": True})
    monkeypatch.setattr(get_settings(), "whapi_api_token", "")
    r = admin_client.post(f"{API}/{body['id']}/sends/{send_id}/retry")
    assert r.status_code == 409 and r.json()["detail"] == "WhatsApp isn't connected"


def test_idempotency_key_returns_the_first_batch(admin_client, db, whapi, marketing, db_feature_user):
    body = made(admin_client)
    key = str(uuid.uuid4())
    first = send(admin_client, body["id"], [marketing["a"]], key=key)
    again = send(admin_client, body["id"], [marketing["a"], marketing["b"]], key=key)
    assert first.status_code == again.status_code == 202
    assert again.json()["batch_id"] == first.json()["batch_id"]
    assert [s["id"] for s in again.json()["sends"]] == [s["id"] for s in first.json()["sends"]]
    assert whapi.sent_to() == [CHAT_A]
    assert db.query(FlyerSend).count() == 1
    # A new key is a new send; the same key from someone else is theirs.
    assert send(admin_client, body["id"], [marketing["b"]]).json()["batch_id"] != first.json()["batch_id"]
    theirs = send(db_feature_user, body["id"], [marketing["a"]], key=key)
    assert theirs.status_code == 202 and theirs.json()["batch_id"] != first.json()["batch_id"]
    # Older than 24 h: no longer the same request.
    db.query(FlyerSend).filter(FlyerSend.batch_id == first.json()["batch_id"]).update(
        {"requested_at": utcnow() - dt.timedelta(hours=25)})
    db.commit()
    assert send(admin_client, body["id"], [marketing["a"]], key=key).json()["batch_id"] != first.json()["batch_id"]


@pytest.fixture
def db_feature_user(db):
    return client_for(db, make_user(db, "meera", features=["marketing_flyers"]))


def test_whatsapp_not_configured_is_409(admin_client, db, marketing, monkeypatch):
    monkeypatch.setattr(get_settings(), "whapi_api_token", "")
    body = made(admin_client)
    r = send(admin_client, body["id"], [marketing["a"]])
    assert r.status_code == 409 and r.json()["detail"] == "WhatsApp isn't connected"
    assert db.query(FlyerSend).count() == 0


@pytest.mark.parametrize("keys, needle", [
    (["nope"], "can't go to"),
    (["deli_staff"], "Deli staff"),
    (["store"], "Store staff group"),
    ([], "at least one"),
    ([f"g{n}" for n in range(26)], "at most 25"),
])
def test_bad_groups_are_400(admin_client, db, whapi, marketing, keys, needle):
    body = made(admin_client)
    r = send(admin_client, body["id"], keys)
    assert r.status_code == 400 and needle in r.json()["detail"], r.text
    assert db.query(FlyerSend).count() == 0 and whapi.sends == []


def test_inactive_marketing_group_is_400(admin_client, whapi, marketing):
    admin_client.patch(f"{GROUPS}/{marketing['a']}", json={"active": False})
    body = made(admin_client)
    assert send(admin_client, body["id"], [marketing["a"]]).status_code == 400


def test_send_body_validation(admin_client, whapi, marketing):
    body = made(admin_client)
    url = f"{API}/{body['id']}/send"
    assert admin_client.post(url, json={"group_keys": [marketing["a"]]}).status_code == 400
    assert admin_client.post(url, json={"group_keys": [marketing["a"]], "idempotency_key": ""}).status_code == 400
    assert admin_client.post(url, json={"group_keys": [marketing["a"]], "idempotency_key": "k" * 101}).status_code == 400
    assert admin_client.post(url, json={"group_keys": "customers_vernon", "idempotency_key": "k"}).status_code == 400
    assert admin_client.post(url, json={"group_keys": [1], "idempotency_key": "k"}).status_code == 400
    assert admin_client.post(f"{API}/999/send", json={"group_keys": [marketing["a"]], "idempotency_key": "k"}).status_code == 404
    # Duplicates in the list are one group.
    r = admin_client.post(url, json={"group_keys": [marketing["a"], marketing["a"]], "idempotency_key": "dup"})
    assert r.status_code == 202 and len(r.json()["sends"]) == 1


def test_timeout_that_went_through_is_settled_as_sent(admin_client, db, whapi, marketing, pauses):
    body = made(admin_client)
    whapi.plan[CHAT_A] = ["late"]
    send(admin_client, body["id"], [marketing["a"]])
    row = db.query(FlyerSend).one()
    assert row.status == "sent" and row.provider_message_id == "msg-1" and not row.ambiguous
    assert whapi.lists == [CHAT_A] and len(whapi.sends) == 1


def test_timeout_not_found_is_ambiguous_and_retry_checks_the_group_first(admin_client, db, whapi, marketing, pauses):
    body = made(admin_client)
    whapi.plan[CHAT_A] = ["timeout"]
    out = send(admin_client, body["id"], [marketing["a"]]).json()
    row = db.query(FlyerSend).one()
    assert row.status == "failed" and row.ambiguous and "may still have gone out" in row.error
    # Looked three times, five seconds apart, before giving up.
    assert whapi.lists == [CHAT_A] * 3 and pauses.count(flyer_sender.RECONCILE_DELAY_SECONDS) == 2

    # It did arrive after all: the retry finds it and does NOT send again.
    whapi._deliver("image", {"to": CHAT_A, "caption": None})
    r = admin_client.post(f"{API}/{body['id']}/sends/{out['sends'][0]['id']}/retry")
    assert r.status_code == 202
    db.refresh(row)
    assert row.status == "sent" and row.provider_message_id == "msg-1" and len(whapi.sends) == 1


def test_a_message_already_credited_to_another_send_is_not_reused(admin_client, db, whapi, marketing):
    """The same flyer sent to the same group twice in a minute: the second
    one times out and never arrives - the first one's message must not be
    taken as proof that it did."""
    body = made(admin_client)
    send(admin_client, body["id"], [marketing["a"]])
    whapi.plan[CHAT_A] = ["timeout"]
    send(admin_client, body["id"], [marketing["a"]])
    first, second = db.query(FlyerSend).order_by(FlyerSend.id).all()
    assert first.status == "sent" and first.provider_message_id == "msg-1"
    assert second.status == "failed" and second.ambiguous and second.provider_message_id is None


def test_retry_after_an_ambiguous_failure_sends_when_not_in_the_group(admin_client, db, whapi, marketing):
    body = made(admin_client)
    whapi.plan[CHAT_A] = ["timeout"]
    out = send(admin_client, body["id"], [marketing["a"]]).json()
    admin_client.post(f"{API}/{body['id']}/sends/{out['sends'][0]['id']}/retry")
    row = db.query(FlyerSend).one()
    db.refresh(row)
    assert row.status == "sent" and row.attempts == 2 and len(whapi.sends) == 2


@pytest.mark.parametrize("step, ambiguous, needle", [
    ("drop", False, "Couldn't reach WhatsApp"),
    (502, True, "may still have gone out"),
    (401, False, "check the Whapi token"),
    (429, False, "slow down"),
    ("unsent", True, "didn't confirm"),
])
def test_send_errors_are_recorded(admin_client, db, whapi, marketing, step, ambiguous, needle):
    body = made(admin_client)
    whapi.plan[CHAT_A] = [step]
    send(admin_client, body["id"], [marketing["a"]])
    row = db.query(FlyerSend).one()
    assert row.status == "failed" and row.ambiguous is ambiguous and needle in row.error, row.error
    assert len(row.error) <= flyer_sender.MAX_ERROR


def test_upload_failure_sends_the_file_inline(admin_client, whapi, marketing):
    whapi.upload_status = 500
    body = made(admin_client)
    send(admin_client, body["id"], [marketing["a"], marketing["b"]])
    assert len(whapi.uploads) == 1, "a failed upload isn't retried for every group"
    for s in whapi.sends:
        assert s["media"].startswith("data:image/jpeg;name=Diwali_Garlands.jpg;base64,")
    import base64

    raw = base64.b64decode(whapi.sends[0]["media"].split(",", 1)[1])
    assert hashlib_sha(raw) == stored_sha(body)


def hashlib_sha(data):
    import hashlib

    return hashlib.sha256(data).hexdigest()


def stored_sha(body):
    from app.db import SessionLocal

    s = SessionLocal()
    try:
        return s.get(Flyer, body["id"]).sha256
    finally:
        s.close()


def test_refused_media_id_falls_back_to_inline(admin_client, db, whapi, marketing):
    body = made(admin_client)
    whapi.plan[CHAT_A] = [400, "ok"]
    send(admin_client, body["id"], [marketing["a"], marketing["b"]])
    assert [s["media"][:5] for s in whapi.sends] == [whapi.upload_id[:5], "data:", "data:"]
    assert [r.status for r in db.query(FlyerSend).order_by(FlyerSend.id)] == ["sent", "sent"]


def test_a_group_refusing_the_file_inline_too_keeps_the_media_id(admin_client, db, whapi, marketing):
    """A group that refuses the MediaID AND the file inline (it's gone, not
    found) was the problem, not the MediaID: the next groups still get the
    one uploaded MediaID instead of a full base64 copy each (found end to
    end on Oct 4 with a mock Whapi: one 404 group sent every later group,
    for 6 hours, the file inline)."""
    body = made(admin_client)
    whapi.plan[CHAT_A] = [404, 404]
    send(admin_client, body["id"], [marketing["a"], marketing["b"]])
    assert [(s["to"], s["media"][:5]) for s in whapi.sends] == [
        (CHAT_A, whapi.upload_id[:5]),
        (CHAT_A, "data:"),
        (CHAT_B, whapi.upload_id[:5]),
    ]
    assert len(whapi.uploads) == 1
    rows = db.query(FlyerSend).order_by(FlyerSend.id).all()
    assert [(r.target_key, r.status) for r in rows] == [(marketing["a"], "failed"), (marketing["b"], "sent")]
    assert "404" in rows[0].error and not rows[0].ambiguous


def test_flyer_deleted_or_group_unticked_before_sending(admin_client, db, whapi, marketing, monkeypatch):
    body = made(admin_client)
    # Queue without running, then change things before the worker gets there.
    queued = []
    monkeypatch.setattr(flyer_sender, "enqueue", queued.extend)
    send(admin_client, body["id"], [marketing["a"], marketing["b"]])
    admin_client.patch(f"{GROUPS}/{marketing['b']}", json={"marketing": False})
    for send_id in queued:
        flyer_sender.process_send(send_id)
    rows = {r.target_key: r for r in db.query(FlyerSend)}
    assert rows[marketing["a"]].status == "sent"
    assert rows[marketing["b"]].status == "failed" and "Use for flyers" in rows[marketing["b"]].error
    assert whapi.sent_to() == [CHAT_A]

    other = made(admin_client, title="Gone soon")
    queued.clear()
    send(admin_client, other["id"], [marketing["a"]])
    admin_client.delete(f"{API}/{other['id']}")
    flyer_sender.process_send(queued[0])
    row = db.query(FlyerSend).filter(FlyerSend.flyer_id == other["id"]).one()
    assert row.status == "failed" and "deleted" in row.error
    assert whapi.sent_to() == [CHAT_A]


def test_flyer_deleted_during_the_pause_between_groups_is_not_sent(admin_client, db, whapi, marketing, monkeypatch):
    body = made(admin_client)

    def pause_then_delete(seconds):
        # Someone deletes the flyer while the sender waits between groups.
        from app.services import flyers as svc
        from app.db import SessionLocal

        s = SessionLocal()
        try:
            svc.delete_flyer(s, admin_client.user, body["id"])
        finally:
            s.close()

    monkeypatch.setattr(flyer_sender, "sleep", pause_then_delete)
    send(admin_client, body["id"], [marketing["a"], marketing["b"]])
    rows = {r.target_key: r for r in db.query(FlyerSend)}
    assert rows[marketing["a"]].status == "sent"
    assert rows[marketing["b"]].status == "failed" and "deleted" in rows[marketing["b"]].error
    assert whapi.sent_to() == [CHAT_A]


def test_worker_processes_a_row_once(admin_client, db, whapi, marketing, monkeypatch):
    body = made(admin_client)
    queued = []
    monkeypatch.setattr(flyer_sender, "enqueue", queued.extend)
    send(admin_client, body["id"], [marketing["a"]])
    flyer_sender.process_send(queued[0])
    flyer_sender.process_send(queued[0])  # e.g. queued twice
    assert len(whapi.sends) == 1


def test_no_push_in_quiet_hours(admin_client, whapi, marketing, pushes, monkeypatch):
    monkeypatch.setattr(flyer_sender, "utcnow", lambda: NIGHT)
    body = made(admin_client)
    send(admin_client, body["id"], [marketing["a"]])
    assert pushes == []


def test_flyer_sent_alert_is_registered_for_the_feature(db, admin):
    alert = notifications.ALERTS_BY_KEY["flyer_sent"]
    assert alert["features"] == ["marketing_flyers"] and alert["route"] == "flyers"
    assert notifications.user_can_access_alert(admin, "flyer_sent")
    seller = make_user(db, "seller", features=["marketing_flyers"])
    other = make_user(db, "other", features=["team_chat"])
    assert notifications.user_can_access_alert(seller, "flyer_sent")
    assert not notifications.user_can_access_alert(other, "flyer_sent")
    prefs = client_for(db, seller).get("/api/notifications/prefs").json()["items"]
    assert "flyer_sent" in [p["key"] for p in prefs]
    assert "flyer_sent" not in [p["key"] for p in client_for(db, other).get("/api/notifications/prefs").json()["items"]]


def test_real_dispatch_reaches_only_the_sender(db, monkeypatch):
    """flyer_sender -> notifications.dispatch_alert: only the requester's
    devices, deduped per (batch, counts)."""
    from app.models import DeviceToken
    from app.services import push

    sender = make_user(db, "sender", features=["marketing_flyers"])
    bystander = make_user(db, "bystander", features=["marketing_flyers"])
    for user in (sender, bystander):
        db.add(DeviceToken(user_id=user.id, token=f"tok-{user.username}", platform="ios", created_at=utcnow(),
                           last_seen_at=utcnow()))
        notifications.set_pref(db, user, "flyer_sent", True)  # off by default since Oct 15
    flyer = Flyer(title="Mango", category="groceries", file_rel_path="Groceries/x.jpg", mime="image/jpeg",
                  size_bytes=1, sha256="0" * 64, created_at=utcnow())
    db.add(flyer)
    db.commit()
    db.add(FlyerSend(flyer_id=flyer.id, batch_id="b-1", channel="whatsapp", target_key="g", target_label="G",
                     status="sent", requested_by_user_id=sender.id, requested_by_name="sender", requested_at=utcnow()))
    db.commit()
    delivered = []
    monkeypatch.setattr(push, "is_configured", lambda: True)
    monkeypatch.setattr(push, "send_to_tokens", lambda tokens, title, body, data: delivered.append((tokens, title, data))
                        or push.SendResult(success_count=len(tokens), delivered_tokens=list(tokens)))
    monkeypatch.setattr(flyer_sender, "dispatch_alert", notifications.dispatch_alert)
    assert flyer_sender._notify_if_batch_done(db, "b-1") == 1
    assert delivered == [(["tok-sender"], "Flyer sent to 1 group", {"type": "flyer_sent", "route": "flyers",
                                                                    "flyer_id": str(flyer.id), "batch_id": "b-1"})]
    # Same counts again: deduped.
    assert flyer_sender._notify_if_batch_done(db, "b-1") == 0


# --- Stale-row recovery ---------------------------------------------------------------------------


def _row(db, flyer_id, batch, status, minutes_ago, key="g", attempted=True):
    at = NOON - dt.timedelta(minutes=minutes_ago)
    row = FlyerSend(flyer_id=flyer_id, batch_id=batch, channel="whatsapp", target_key=key, target_label=key,
                    target_ref=CHAT_A, status=status, requested_by_user_id=1, requested_by_name="boss",
                    requested_at=at, queued_at=at, attempted_at=at if attempted and status != "queued" else None)
    db.add(row)
    db.commit()
    return row


def test_startup_recovery_fails_stale_rows_only(db):
    old_q = _row(db, 1, "old", "queued", 40, key="a")
    old_s = _row(db, 1, "old", "sending", 30, key="b")
    fresh = _row(db, 1, "fresh", "queued", 5)
    # A batch another live process is still working through: one row was
    # attempted 2 minutes ago, so its queued rows (queued long ago) are left.
    busy_q = _row(db, 1, "busy", "queued", 50, key="a")
    busy_s = _row(db, 1, "busy", "sending", 2, key="b")
    done = _row(db, 1, "done", "sent", 60)
    owned = _row(db, 1, "mine", "queued", 45)
    changed = flyer_sender.recover_stuck_sends(db, now=NOON, owned={owned.id})
    assert changed == 2
    for row in (old_q, old_s, fresh, busy_q, busy_s, done, owned):
        db.refresh(row)
    assert (old_q.status, old_q.error, old_q.ambiguous) == ("failed", "Interrupted - retry", False)
    # It was mid-send: it may have gone out, so a retry checks the group first.
    assert (old_s.status, old_s.error, old_s.ambiguous) == ("failed", "Interrupted - retry", True)
    assert [r.status for r in (fresh, busy_q, busy_s, done, owned)] == ["queued", "queued", "sending", "sent", "queued"]
    # Nothing left to do the second time.
    assert flyer_sender.recover_stuck_sends(db, now=NOON, owned={owned.id}) == 0


def test_start_runs_recovery_and_never_raises(db, monkeypatch):
    stale = _row(db, 1, "old", "sending", 30)
    started = []
    monkeypatch.setattr(flyer_sender, "_ensure_thread", lambda: started.append(True))
    monkeypatch.setattr(flyer_sender, "utcnow", lambda: NOON)
    flyer_sender.start()
    db.refresh(stale)
    assert stale.status == "failed" and started == [True]

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(flyer_sender, "recover_stuck_sends", boom)
    flyer_sender.start()  # logged, not raised


def test_recovered_send_can_be_retried(admin_client, db, whapi, marketing):
    body = made(admin_client)
    row = _row(db, body["id"], "old", "sending", 30, key=marketing["a"])
    flyer_sender.recover_stuck_sends(db, now=NOON)
    r = admin_client.post(f"{API}/{body['id']}/sends/{row.id}/retry")
    assert r.status_code == 202
    db.refresh(row)
    # Checked the group first (not there), then sent.
    assert row.status == "sent" and whapi.lists == [CHAT_A] and whapi.sent_to() == [CHAT_A]


def test_real_worker_thread_sends_one_group_at_a_time(admin_client, db, whapi, marketing, monkeypatch, pauses):
    """The real queue + thread (not the inline stand-in the other tests use):
    every send of a batch is made, in order, by one worker."""
    monkeypatch.setattr(flyer_sender, "enqueue", REAL_ENQUEUE)
    monkeypatch.setattr(flyer_sender, "jitter", lambda: 4.0)
    body = made(admin_client)
    try:
        r = send(admin_client, body["id"], [marketing["a"], marketing["b"]])
        assert r.status_code == 202
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with flyer_sender._lock:
                busy = bool(flyer_sender._owned)
            if not busy:
                break
            time.sleep(0.05)
        assert not busy, "the worker didn't finish"
    finally:
        flyer_sender.stop()
    assert whapi.sent_to() == [CHAT_A, CHAT_B]
    assert [r.status for r in db.query(FlyerSend).order_by(FlyerSend.id)] == ["sent", "sent"]
    assert flyer_sender._thread is None
    # The pause between the two groups (recorded, not slept).
    assert len(pauses) == 1 and 3.5 <= pauses[0] <= 4.0


# --- Access ---------------------------------------------------------------------------------------


ROUTES = [
    ("GET", f"{API}/meta"), ("GET", API), ("POST", API), ("GET", f"{API}/1"), ("GET", f"{API}/1/file"),
    ("GET", f"{API}/1/thumb"), ("PATCH", f"{API}/1"), ("DELETE", f"{API}/1"), ("POST", f"{API}/1/send"),
    ("POST", f"{API}/1/sends/1/retry"),
]


def test_every_route_needs_the_feature(db, anon_client):
    everything_else = client_for(db, make_user(db, "all_but", features=sorted(FEATURE_KEYS - {"marketing_flyers"})))
    for method, url in ROUTES:
        assert anon_client.request(method, url, json={}).status_code == 401, url
        r = everything_else.request(method, url, json={})
        assert r.status_code == 403 and r.json()["detail"] == "You don't have access to this feature", url


def test_staff_with_the_feature_can_use_it(db, db_feature_user, whapi, marketing):
    body = made(db_feature_user, category="others")
    assert body["created_by_name"] == "meera"
    assert db_feature_user.get(f"{API}/meta").status_code == 200
    assert send(db_feature_user, body["id"], [marketing["a"]]).status_code == 202
    assert db_feature_user.delete(f"{API}/{body['id']}").status_code == 204


def test_demo_login_is_refused_even_with_the_feature(db, admin_client):
    demo = client_for(db, make_user(db, "app_store_review", features=sorted(FEATURE_KEYS)))
    body = made(admin_client)
    for method, url in ROUTES:
        r = demo.request(method, url.replace("/1", f"/{body['id']}", 1), json={})
        assert r.status_code == 403 and r.json()["detail"] == DEMO_READ_ONLY, (method, url, r.text)


def test_flyer_routes_are_not_in_the_demo_allowlist():
    assert not [k for k in ALLOWLIST if k[1].startswith("/api/flyers")]


def test_feature_key_and_label():
    assert {"key": "marketing_flyers", "label": "Marketing flyers"} in FEATURES
    assert "marketing_flyers" in FEATURE_KEYS


def test_glossary_terms(admin_client):
    terms = {t["key"]: t for t in admin_client.get("/api/glossary").json()["terms"]}
    assert terms["flyer"]["label"] == "Flyer" and terms["marketing_group"]["label"] == "Marketing group"
    assert terms["flyer"]["category"] == terms["marketing_group"]["category"] == "marketing"
    assert "25" in terms["marketing_group"]["rule"] and "20 MB" in terms["flyer"]["rule"]


def test_chat_ids_never_reach_a_client(admin_client, whapi, marketing):
    body = made(admin_client)
    send(admin_client, body["id"], [marketing["a"], marketing["b"]])
    for url in (f"{API}/{body['id']}", API, f"{API}/meta"):
        text = admin_client.get(url).text
        assert "@g.us" not in text and "target_ref" not in text, url


# --- Adversarial review (Oct 4) ------------------------------------------------------------------

CHAT_D = "120363044444444444@g.us"


def test_titles_and_captions_keep_emoji_joiners_and_new_characters(admin_client, db, base):
    """A ZWJ emoji (a cook), an emoji newer than Python's Unicode tables
    (category "Cn" on 3.9 and 3.12) and Indian-script joiners reach WhatsApp
    intact - they used to be dropped as "control" characters. Controls, bidi
    overrides and a BOM still go."""
    cook = "\U0001F469\u200d\U0001F373"  # woman + ZWJ + frying pan
    roots = "\U0001FADC"  # root vegetable, Unicode 16
    hindi = "\u0915\u094d\u200d\u0937 \u0930\u0941\u200c\u092a\u092f\u0947"  # a ZWJ half form, a ZWNJ
    caption = f"{cook} Kitchen special!\n{roots} fresh roots {hindi}"
    body = made(admin_client, title=f"Chef {cook} {roots}", caption=caption)
    assert body["caption"] == caption and body["title"] == f"Chef {cook} {roots}"
    path, _ = stored(base, body, db)
    assert path.name == f"{path.name[:10]} Chef {cook} {roots} ({body['id']}).jpg"
    # A line break or tab in a title is a space (not glued words); controls,
    # bidi overrides and a BOM are dropped.
    r = admin_client.patch(f"{API}/{body['id']}",
                           json={"title": "Diwali\u202e\ufeff\nSale\tToday\x07", "caption": "Hi\u202e there\x00"})
    assert r.status_code == 200, r.text
    assert (r.json()["title"], r.json()["caption"]) == ("Diwali Sale Today", "Hi there")
    # Invisible-only text is no title / no message.
    assert admin_client.patch(f"{API}/{body['id']}", json={"title": "\u200d\u200b "}).status_code == 400
    assert admin_client.patch(f"{API}/{body['id']}", json={"caption": "\u200d\u2060"}).json()["caption"] is None
    # File names also drop invisible format characters and noncharacters, but keep the joiners.
    assert flyers.safe_file_title(f"A\u200bB{cook}\u2060\ufdd0\uffff") == f"AB{cook}"
    assert flyers.safe_file_title("\u200d\u200b") == "Flyer"


def test_out_of_range_ids_are_404_not_500(admin_client, whapi, marketing):
    """A huge id in the URL used to reach SQLite and raise OverflowError (500)."""
    body = made(admin_client)
    huge = 2**64
    payload = {"title": "x", "group_keys": [marketing["a"]], "idempotency_key": "k"}
    for method, url in (("GET", f"{API}/{huge}"), ("GET", f"{API}/{huge}/file"), ("GET", f"{API}/{huge}/thumb"),
                        ("PATCH", f"{API}/{huge}"), ("DELETE", f"{API}/{huge}"), ("POST", f"{API}/{huge}/send"),
                        ("POST", f"{API}/{huge}/sends/1/retry"), ("POST", f"{API}/{body['id']}/sends/{huge}/retry"),
                        ("GET", f"{API}/-5"), ("GET", f"{API}/0")):
        r = admin_client.request(method, url, json=payload)
        assert r.status_code == 404, (method, url, r.status_code, r.text)
    r = admin_client.get(API, params={"before_id": huge})
    assert r.status_code == 200 and [f["id"] for f in r.json()["items"]] == [body["id"]]


def test_file_name_date_is_the_store_date_not_utc(admin_client, db, base, monkeypatch):
    monkeypatch.setattr(flyers, "utcnow", lambda: NIGHT)  # Oct 6 03:30 UTC = Oct 5 23:30 in the store
    body = made(admin_client, title="Late upload")
    path, _ = stored(base, body, db)
    assert path.parent.name == "Flowers" and path.name == f"2026-10-05 Late upload ({body['id']}).jpg"
    r = admin_client.patch(f"{API}/{body['id']}", json={"title": "Renamed", "category": "others"})
    path, _ = stored(base, r.json(), db)
    assert path.parent.name == "Others" and path.name == f"2026-10-05 Renamed ({body['id']}).jpg"


def test_a_queued_send_never_goes_to_a_chat_that_was_swapped_out(admin_client, db, whapi, marketing, monkeypatch):
    """An admin replaces a group's WhatsApp chat (removes the group and adds
    it again under the same name - same key) while a flyer to it is still
    queued. The old chat isn't a marketing group any more: it must not get
    the flyer (it used to - the row kept the old chat id)."""
    held = []
    monkeypatch.setattr(flyer_sender, "enqueue", held.extend)
    body = made(admin_client)
    assert send(admin_client, body["id"], [marketing["a"]]).status_code == 202
    assert admin_client.delete(f"{GROUPS}/{marketing['a']}").status_code == 204
    r = admin_client.post(GROUPS, json={"chat_id": CHAT_D, "label": "Customers Vernon", "marketing": True})
    assert r.status_code == 200 and r.json()["key"] == marketing["a"]
    for send_id in held:
        flyer_sender.process_send(send_id)
    row = db.query(FlyerSend).one()
    assert whapi.sends == [] and row.status == "failed" and "chat was changed" in row.error
    assert "@g.us" not in admin_client.get(f"{API}/{body['id']}").text
    # A retry goes to the group's chat as it is now.
    monkeypatch.setattr(flyer_sender, "enqueue", lambda ids: [flyer_sender.process_send(i) for i in ids])
    assert admin_client.post(f"{API}/{body['id']}/sends/{row.id}/retry").status_code == 202
    db.refresh(row)
    assert row.status == "sent" and whapi.sent_to() == [CHAT_D]


def test_a_retry_while_the_worker_finishes_the_failed_send_is_not_dropped(
    admin_client, db, whapi, marketing, monkeypatch
):
    """The real queue + thread. Retry is tapped while the worker is still
    wrapping up the failed attempt (sending the batch's push): the retry
    used to be skipped as "already queued" and sat in `queued` until the
    stale sweep failed it 15+ minutes later."""
    monkeypatch.setattr(flyer_sender, "enqueue", REAL_ENQUEUE)
    whapi.plan[CHAT_A] = [403]
    body = made(admin_client)
    admin_id = admin_client.user.id
    retried = []

    def dispatch(_db, _alert):
        if not retried:
            s = SessionLocal()
            try:
                row = s.query(FlyerSend).one()
                _, ids = flyers.retry_send(s, s.get(User, admin_id), body["id"], row.id)
                flyer_sender.enqueue(ids)  # what POST .../retry does
                retried.append(ids)
            finally:
                s.close()
        return 1

    monkeypatch.setattr(flyer_sender, "dispatch_alert", dispatch)
    try:
        assert send(admin_client, body["id"], [marketing["a"]]).status_code == 202
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            with flyer_sender._lock:
                busy = bool(flyer_sender._owned)
            if not busy:
                break
            time.sleep(0.05)
    finally:
        flyer_sender.stop()
    assert retried and not busy
    row = db.query(FlyerSend).one()
    db.refresh(row)
    assert (row.status, row.attempts) == ("sent", 2) and whapi.sent_to() == [CHAT_A, CHAT_A]


def test_a_send_that_may_have_gone_out_stays_checked_after_a_refused_retry(
    admin_client, db, whapi, marketing, monkeypatch
):
    """A timed-out send (it may be in the group) is retried while its group
    is briefly unticked. That retry is refused without trying - and must keep
    "may have gone out", so the next retry looks in the group instead of
    posting the flyer a second time (it used to clear the flag)."""
    body = made(admin_client, caption="Weekend sale")
    whapi.plan[CHAT_A] = ["timeout"]
    send(admin_client, body["id"], [marketing["a"]])
    row = db.query(FlyerSend).one()
    assert row.status == "failed" and row.ambiguous
    # It did go out after all - WhatsApp was just slow to show it.
    whapi._deliver("image", {"to": CHAT_A, "caption": "Weekend sale"})
    held = []
    monkeypatch.setattr(flyer_sender, "enqueue", held.extend)
    assert admin_client.post(f"{API}/{body['id']}/sends/{row.id}/retry").status_code == 202
    admin_client.patch(f"{GROUPS}/{marketing['a']}", json={"marketing": False})
    for send_id in held:
        flyer_sender.process_send(send_id)
    db.refresh(row)
    assert row.status == "failed" and "Use for flyers" in row.error and row.ambiguous
    admin_client.patch(f"{GROUPS}/{marketing['a']}", json={"marketing": True})
    monkeypatch.setattr(flyer_sender, "enqueue", lambda ids: [flyer_sender.process_send(i) for i in ids])
    assert admin_client.post(f"{API}/{body['id']}/sends/{row.id}/retry").status_code == 202
    db.refresh(row)
    assert row.status == "sent" and len(whapi.sends) == 1  # found in the group, not posted again


def test_a_group_unticked_while_the_file_uploads_does_not_get_it(admin_client, db, whapi, marketing, monkeypatch):
    """The first group of a run waits for the file to upload to Whapi (up to
    ~2 minutes for a big PDF). A group unticked meanwhile used to get the
    flyer anyway: the last check was before the upload."""
    real_post = whapi.post

    def post(url, **kw):
        if url.endswith("/media"):  # the admin unticks the group mid-upload
            s = SessionLocal()
            try:
                s.query(WhatsAppGroup).filter_by(key=marketing["a"]).update({"marketing": False})
                s.commit()
            finally:
                s.close()
        return real_post(url, **kw)

    monkeypatch.setattr(whapi_client.httpx, "post", post)
    body = made(admin_client)
    send(admin_client, body["id"], [marketing["a"], marketing["b"]])
    rows = {r.target_key: r for r in db.query(FlyerSend)}
    assert rows[marketing["a"]].status == "failed" and "Use for flyers" in rows[marketing["a"]].error
    assert rows[marketing["b"]].status == "sent"
    assert whapi.sent_to() == [CHAT_B] and len(whapi.uploads) == 1


def _chatter(n, at):
    return [{"id": f"c{i}", "from_me": False, "type": "text", "timestamp": at, "text": {"body": "hi"}} for i in range(n)]


def test_reconcile_pages_back_through_a_busy_group(admin_client, db, whapi, marketing):
    """A flyer that timed out but did go out, then 150 customer messages in
    the group: the retry pages back (Whapi's count + offset) and finds it
    instead of posting it a second time - one page of 50 used to miss it."""
    body = made(admin_client, caption="Weekend sale")
    whapi.plan[CHAT_A] = ["timeout"]
    send(admin_client, body["id"], [marketing["a"]])
    row = db.query(FlyerSend).one()
    assert row.status == "failed" and row.ambiguous
    whapi._deliver("image", {"to": CHAT_A, "caption": "Weekend sale"})
    whapi.history[CHAT_A] += _chatter(150, int(NOON.timestamp()) + 600)
    whapi.lists.clear()
    assert admin_client.post(f"{API}/{body['id']}/sends/{row.id}/retry").status_code == 202
    db.refresh(row)
    assert row.status == "sent" and len(whapi.sends) == 1
    assert whapi.lists == [CHAT_A, CHAT_A]  # two pages of 100


def test_reconcile_paging_stops(whapi):
    """Paging stops at the first page older than the attempt, when Whapi
    ignores offset (the same page again), and after RECONCILE_MAX_PAGES."""
    since = NOON.timestamp()
    find = lambda: whapi_client.find_sent_media(CHAT_A, "image", "x", since)  # noqa: E731
    whapi.history[CHAT_A] = _chatter(300, int(since) - 3600)  # all older than the attempt
    assert find() is None and len(whapi.lists) == 1
    whapi.lists.clear()
    whapi.history[CHAT_A] = _chatter(300, int(since) + 60)
    whapi.ignore_offset = True
    assert find() is None and len(whapi.lists) == 2
    whapi.lists.clear()
    whapi.ignore_offset = False
    whapi.history[CHAT_A] = _chatter(2000, int(since) + 60)
    assert find() is None and len(whapi.lists) == whapi_client.RECONCILE_MAX_PAGES


def _xref_stream_pdf(inflated_mb: int, filter_name: str = "/FlateDecode") -> bytes:
    """A small, valid PDF 1.5 (cross-reference stream) whose xref stream
    inflates to `inflated_mb` MB more than it needs: a decompression bomb
    when that's big (pypdf inflates it just to open the file)."""
    out = bytearray(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
    offsets = {}
    for num, obj in ((1, b"<< /Type /Catalog /Pages 2 0 R >>"), (2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
                     (3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>")):
        offsets[num] = len(out)
        out += b"%d 0 obj\n%s\nendobj\n" % (num, obj)
    offsets[4] = len(out)
    entries = struct.pack(">BIH", 0, 0, 65535) + b"".join(struct.pack(">BIH", 1, offsets[n], 0) for n in (1, 2, 3, 4))
    packer = zlib.compressobj(9)
    data = packer.compress(entries) + b"".join(packer.compress(b"\0" * (1024 * 1024)) for _ in range(inflated_mb))
    data += packer.flush()
    out += (b"4 0 obj\n<< /Type /XRef /Size 5 /W [1 4 2] /Root 1 0 R /Filter %s /Length %d >>\nstream\n"
            % (filter_name.encode(), len(data)))
    out += data + b"\nendstream\nendobj\nstartxref\n%d\n%%%%EOF\n" % offsets[4]
    return bytes(out)


def test_pdf_decompression_bomb_is_refused(admin_client, base, monkeypatch):
    """The PDF twin of the pixel-bomb guard: a few KB that pypdf would
    inflate to hundreds of MB (a 20 MB upload: ~20 GB) just to open it."""
    monkeypatch.setattr(flyers, "PDF_CHECK_INFLATE_BYTES", 2 * 1024 * 1024)
    ok = upload(admin_client, _xref_stream_pdf(0), name="ok.pdf", content_type="application/pdf")
    assert ok.status_code == 201 and ok.json()["mime"] == "application/pdf", ok.text
    bomb = _xref_stream_pdf(8)
    assert len(bomb) < 64 * 1024
    for data in (bomb, _xref_stream_pdf(0, "/LZWDecode"), _xref_stream_pdf(0, "/RunLengthDecode")):
        r = upload(admin_client, data, name="bomb.pdf", content_type="application/pdf")
        assert r.status_code == 400 and r.json()["detail"] == flyers.PDF_TOO_COMPLEX, r.text
    assert len(list(base.rglob("*.pdf"))) == 1  # only the good one was stored
    # Only that check is capped: pypdf elsewhere (invoice PDFs) inflates as before.
    import pypdf.filters

    big = zlib.compress(b"\0" * (8 * 1024 * 1024))
    assert len(pypdf.filters.decompress(big)) == 8 * 1024 * 1024
    # Real PDFs (classic xref tables) are unaffected under the small cap too.
    assert upload(admin_client, pdf(3), name="m.pdf", content_type="application/pdf").status_code == 201


def test_every_flyer_route_in_the_app_is_gated(db, anon_client, admin_client):
    """Enumerates app.routes rather than a hand-kept list, so a route added
    later is covered: 401 logged out, 403 without the feature, and the demo
    login's 403 even with every feature ticked."""
    from app.main import app

    body = made(admin_client)
    routes = sorted(
        (method, route.path)
        for route in app.routes
        if getattr(route, "path", "").startswith(API)
        for method in (getattr(route, "methods", None) or ())
    )
    assert len(routes) >= len(ROUTES)  # at least the known ten
    no_feature = client_for(db, make_user(db, "no_flyers", features=sorted(FEATURE_KEYS - {"marketing_flyers"})))
    demo = client_for(db, make_user(db, "app_store_review", features=sorted(FEATURE_KEYS)))
    for method, path in routes:
        url = path.replace("{flyer_id}", str(body["id"])).replace("{send_id}", "1")
        assert "{" not in url, f"new path parameter in {path}: extend this test"
        assert anon_client.request(method, url, json={}).status_code == 401, (method, path)
        r = no_feature.request(method, url, json={})
        assert r.status_code == 403 and r.json()["detail"] == "You don't have access to this feature", (method, path)
        r = demo.request(method, url, json={})
        assert r.status_code == 403 and r.json()["detail"] == DEMO_READ_ONLY, (method, path)
    assert admin_client.get(f"{API}/{body['id']}").json()["title"] == body["title"]


# --- Migration 0046 -------------------------------------------------------------------------------


def _alembic(db_file, *args):
    env = dict(os.environ, DATABASE_URL=f"sqlite:///{db_file}")
    alembic = pathlib.Path(sys.executable).parent / "alembic"
    return subprocess.run([str(alembic), *args], cwd=BACKEND, env=env, check=True, capture_output=True, text=True)


def _copy(src, dst):
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        s.close()
        d.close()


@pytest.mark.parametrize("which", ["fresh", "dev"])
def test_migration_0046_up_down_up(tmp_path, which):
    db_file = tmp_path / f"{which}.db"
    if which == "dev":
        src = BACKEND / "spicetown-dev.db"
        if not src.exists():
            pytest.skip("no dev database here")
        _copy(src, db_file)
    _alembic(db_file, "upgrade", "0045")
    con = sqlite3.connect(db_file)
    groups_before = con.execute("SELECT id, key, label, chat_id, builtin, active FROM whatsapp_groups ORDER BY id").fetchall()
    con.close()

    _alembic(db_file, "upgrade", "head")
    con = sqlite3.connect(db_file)
    try:
        for table in ("flyers", "flyer_sends", "whatsapp_groups"):
            migrated = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
            assert migrated == {c.name for c in Base.metadata.tables[table].columns}, table
        # Every existing group (the built-in staff groups included) starts unticked.
        assert con.execute("SELECT id, key, label, chat_id, builtin, active FROM whatsapp_groups ORDER BY id").fetchall() == groups_before
        assert {r[0] for r in con.execute("SELECT DISTINCT marketing FROM whatsapp_groups")} <= {0}
        for table in ("flyers", "flyer_sends"):
            sql = con.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()[0]
            assert "AUTOINCREMENT" in sql, f"{table} must never reuse ids"
        con.execute("INSERT INTO whatsapp_groups (key, label, created_at) VALUES ('new_one', 'New', '2026-10-04')")
        assert con.execute("SELECT marketing FROM whatsapp_groups WHERE key = 'new_one'").fetchone() == (0,)
        con.execute(
            "INSERT INTO flyer_sends (flyer_id, batch_id, target_key, target_label, status, requested_at) "
            "VALUES (1, 'b', 'g', 'G', 'queued', '2026-10-04')"
        )
        row = con.execute("SELECT channel, attempts, ambiguous FROM flyer_sends").fetchone()
        assert row == ("whatsapp", 0, 0)
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(
                "INSERT INTO flyer_sends (flyer_id, batch_id, target_key, target_label, status, requested_at) "
                "VALUES (1, 'b', 'g', 'G', 'queued', '2026-10-04')"
            )
        con.rollback()
    finally:
        con.close()

    _alembic(db_file, "downgrade", "0045")
    con = sqlite3.connect(db_file)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "flyers" not in tables and "flyer_sends" not in tables
        assert "marketing" not in {r[1] for r in con.execute("PRAGMA table_info(whatsapp_groups)")}
        assert con.execute("SELECT id, key, label, chat_id, builtin, active FROM whatsapp_groups ORDER BY id").fetchall() == groups_before
        # The unique key survives the table rebuild.
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO whatsapp_groups (key, label, created_at) VALUES ('store', 'x', '2026-10-01')")
    finally:
        con.close()
    _alembic(db_file, "upgrade", "head")
    con = sqlite3.connect(db_file)
    try:
        assert con.execute("SELECT version_num FROM alembic_version").fetchone()[0] >= "0046"
        assert "marketing" in {r[1] for r in con.execute("PRAGMA table_info(whatsapp_groups)")}
    finally:
        con.close()
