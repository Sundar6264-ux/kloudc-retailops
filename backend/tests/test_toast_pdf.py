"""toast_pdf: the Letter-sized, searchable copy of an invoice attached to the
Toast email. Nothing here sends a real email - SMTP is replaced by a fake."""

import email
import hashlib
import io
import pathlib
import types

import pytest
from PIL import Image, ImageDraw, ImageFont
from pypdf import PdfReader

from app.services import toast_email, toast_pdf
from app.services.toast_pdf import split_tall_image, toast_friendly_pdf

needs_tesseract = pytest.mark.skipif(toast_pdf.find_tesseract() is None, reason="tesseract not installed")

LETTER = (612.0, 792.0)


def _page_sizes(data_or_path):
    src = io.BytesIO(data_or_path) if isinstance(data_or_path, bytes) else str(data_or_path)
    return [(float(p.mediabox.width), float(p.mediabox.height)) for p in PdfReader(src).pages]


def _sha(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _receipt(width=400, lines=40, line_h=24, gap=16, color=(235, 235, 230)):
    """A tall grey 'receipt' with dark text-line bars separated by gaps.
    Returns the image and the (top, bottom) rows of every line."""
    height = 30 + lines * (line_h + gap)
    img = Image.new("RGB", (width, height), color)
    draw = ImageDraw.Draw(img)
    spans = []
    y = 20
    for i in range(lines):
        draw.rectangle((20, y, width - 20 - (i % 5) * 30, y + line_h - 1), fill=(40, 40, 40))
        spans.append((y, y + line_h - 1))
        y += line_h + gap
    return img, spans


def _image_pdf(path, img, resolution=72.0):
    img.save(path, "PDF", resolution=resolution)
    return path


def _text_image(lines, size=(1700, 2200)):
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=56)
    y = 120
    for line in lines:
        draw.text((120, y), line, fill="black", font=font)
        y += 110
    return img


# ------------------------------------------------------------ tall splitting


def test_tall_image_is_split_into_letter_chunks_between_lines():
    img, spans = _receipt()
    assert img.height / img.width > 3
    chunks = split_tall_image(img)

    assert len(chunks) > 1
    assert sum(c.height for c in chunks) == img.height  # clean cuts: no overlap, nothing lost
    for c in chunks:
        assert c.width == img.width
        assert c.height / c.width <= toast_pdf.MAX_SANE_ASPECT
    # Every cut row falls in a gap, never inside a text line.
    cut = 0
    for c in chunks[:-1]:
        cut += c.height
        assert not any(top <= cut <= bottom for top, bottom in spans), cut


def test_split_without_gaps_overlaps_so_no_line_is_lost():
    img = Image.effect_noise((300, 1500), 120).convert("RGB")  # ink everywhere, no gaps
    chunks = split_tall_image(img)
    assert len(chunks) > 1
    assert sum(c.height for c in chunks) > img.height  # the chunks overlap


def test_normal_shaped_image_is_not_split():
    img = Image.new("RGB", (850, 1100), "white")
    assert split_tall_image(img) == [img]


# ----------------------------------------------------------------- passthrough


def test_normal_letter_pdf_passes_through_unchanged(tmp_path):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    src = tmp_path / "adobe.pdf"
    c = canvas.Canvas(str(src), pagesize=letter)
    for i in range(30):
        c.drawString(72, 720 - i * 20, f"Line {i} INVOICE 1001 VENDOR ACME TOTAL $123.45")
    c.save()
    before = _sha(src)

    with toast_friendly_pdf(src) as out:
        assert out == src
    assert _sha(src) == before


def test_image_only_letter_pdf_passes_through(tmp_path):
    # CamScanner shape: image-only but a normal 612x792 page - Toast reads these.
    src = _image_pdf(tmp_path / "cam.pdf", Image.new("RGB", (1700, 2200), "white"), resolution=200.0)
    assert _page_sizes(src) == [LETTER]
    with toast_friendly_pdf(src) as out:
        assert out == src


# ---------------------------------------------------------------- conversion


def test_tall_app_scan_becomes_letter_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(toast_pdf, "find_tesseract", lambda: None)  # geometry only, fast
    img, _ = _receipt()
    src = _image_pdf(tmp_path / "app.pdf", img)
    assert _page_sizes(src)[0][1] / _page_sizes(src)[0][0] > 3
    before = _sha(src)

    with toast_friendly_pdf(src) as out:
        assert out != src
        sizes = _page_sizes(out)
        workdir = out.parent
    assert len(sizes) > 1
    assert all(s == LETTER for s in sizes)
    assert _sha(src) == before  # stored original untouched
    assert not workdir.exists()  # temp copy cleaned up


def test_mixed_odd_pages_all_become_letter(tmp_path, monkeypatch):
    monkeypatch.setattr(toast_pdf, "find_tesseract", lambda: None)
    pages = [Image.new("RGB", (1516, 2000), "white"), Image.new("RGB", (995, 2000), "white"), Image.new("RGB", (1868, 2000), "white")]
    src = tmp_path / "mixed.pdf"
    pages[0].save(src, "PDF", save_all=True, append_images=pages[1:], resolution=72.0)
    with toast_friendly_pdf(src) as out:
        sizes = _page_sizes(out)
    assert len(sizes) == 4  # the 2:1 page is split in two
    assert all(s == LETTER for s in sizes)


def test_camscanner_logo_image_is_ignored(tmp_path, monkeypatch):
    # A page with the scan plus a small logo: only the big image is used.
    monkeypatch.setattr(toast_pdf, "find_tesseract", lambda: None)
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    big, _ = _receipt(lines=60)
    logo = Image.new("RGB", (240, 90), "red")
    src = tmp_path / "cam_tall.pdf"
    c = canvas.Canvas(str(src), pagesize=(416, 3316))
    c.drawImage(ImageReader(big), 0, 0, width=416, height=3264)
    c.drawImage(ImageReader(logo), 300, 3270, width=100, height=40)
    c.save()

    images = toast_pdf._images_from_pdf(src)
    assert [i.size for i in images] == [big.size]


def test_photo_upload_becomes_single_letter_page(tmp_path, monkeypatch):
    monkeypatch.setattr(toast_pdf, "find_tesseract", lambda: None)
    src = tmp_path / "photo.jpg"
    Image.new("RGB", (1320, 688), "white").save(src, "JPEG")
    with toast_friendly_pdf(src) as out:
        assert out.suffix == ".pdf"
        assert _page_sizes(out) == [LETTER]


def test_output_is_reencoded_smaller_when_over_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(toast_pdf, "find_tesseract", lambda: None)
    noisy = Image.effect_noise((400, 1600), 60).convert("RGB")
    src = _image_pdf(tmp_path / "noisy.pdf", noisy)
    with toast_friendly_pdf(src) as out:
        normal = out.stat().st_size
    assert normal <= toast_pdf.MAX_OUTPUT_BYTES
    monkeypatch.setattr(toast_pdf, "MAX_OUTPUT_BYTES", normal // 3)
    with toast_friendly_pdf(src) as out:
        smaller = out.stat().st_size
        assert all(s == LETTER for s in _page_sizes(out))
    assert smaller < normal


# ---------------------------------------------------------------------- OCR


@needs_tesseract
def test_ocr_adds_searchable_text_layer(tmp_path):
    img = _text_image(["ACME FOODS INC", "INVOICE NO 48213", "DATE 09/25/2026", "TOTAL DUE 1960.44"])
    # Make it tall and grey like an app scan so it gets converted.
    tall = Image.new("RGB", (img.width, img.width * 3), (225, 225, 220))
    tall.paste(img.convert("L").point(lambda v: 225 if v > 128 else 30).convert("RGB"), (0, 0))
    src = _image_pdf(tmp_path / "app.pdf", tall)
    assert sum(len(p.extract_text() or "") for p in PdfReader(str(src)).pages) == 0

    with toast_friendly_pdf(src) as out:
        text = "\n".join(p.extract_text() or "" for p in PdfReader(str(out)).pages)
        assert all(s == LETTER for s in _page_sizes(out))
    assert "ACME" in text
    assert "48213" in text
    assert "1960.44" in text


# ------------------------------------------------------------------ fallback


def test_corrupt_pdf_falls_back_to_original(tmp_path):
    src = tmp_path / "broken.pdf"
    src.write_bytes(b"%PDF-1.4 this is not really a pdf")
    with toast_friendly_pdf(src) as out:
        assert out == src


def test_unexpected_error_falls_back_to_original(tmp_path, monkeypatch):
    img, _ = _receipt()
    src = _image_pdf(tmp_path / "app.pdf", img)

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(toast_pdf, "_assemble", boom)
    with toast_friendly_pdf(src) as out:
        assert out == src


def test_ocr_failure_still_sends_letter_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(toast_pdf, "find_tesseract", lambda: "/nonexistent/tesseract")
    img, _ = _receipt()
    src = _image_pdf(tmp_path / "app.pdf", img)
    with toast_friendly_pdf(src) as out:
        assert out != src
        assert all(s == LETTER for s in _page_sizes(out))


def test_pdf_without_images_falls_back(tmp_path):
    from reportlab.pdfgen import canvas

    src = tmp_path / "vector_tall.pdf"
    c = canvas.Canvas(str(src), pagesize=(595, 3000))
    c.drawString(72, 2900, "no images here")
    c.save()
    with toast_friendly_pdf(src) as out:
        assert out == src


# ------------------------------------------------------- image quality (W6)


def _page_picture(pdf_path, index=0):
    """(PIL image, raw JPEG bytes, drawn rect (x, y, w, h) in points) of a
    converted page's one picture."""
    import re

    page = PdfReader(str(pdf_path)).pages[index]
    xobjects = page["/Resources"]["/XObject"]
    raw = None
    for name in xobjects:
        obj = xobjects[name].get_object()
        if obj.get("/Subtype") == "/Image":
            raw = obj._data
            assert obj.get("/Filter") == "/DCTDecode"
    image = max((i.image for i in page.images), key=lambda i: i.width * i.height)
    content = page.get_contents().get_data().decode("latin-1")
    outer = re.search(r"([\d.]+) 0(?:\.0)? 0(?:\.0)? ([\d.]+) ([\d.]+) ([\d.]+) cm", content)
    sx, sy, tx, ty = (float(v) for v in outer.groups())
    return image, raw, (tx, ty, sx * image.width, sy * image.height)


def _jpeg_quality(raw):
    """libjpeg-style quality from the luma table (order-free estimate)."""
    table = Image.open(io.BytesIO(raw)).quantization[0]
    std = 3360  # sum of the standard luma table
    ratio = sum(table) / std * 100
    return (200 - ratio) / 2 if ratio <= 100 else 5000 / ratio


def test_small_photo_is_not_blown_up_to_300_dpi(tmp_path, monkeypatch):
    # The old pages scaled every chunk UP to a full 300 dpi Letter canvas:
    # a 1320 px wide photo became a 2550x3300 JPEG of interpolated pixels.
    monkeypatch.setattr(toast_pdf, "find_tesseract", lambda: None)
    src = tmp_path / "photo.jpg"
    _text_image(["SMALL PHOTO 12.50"], size=(1320, 688)).save(src, "JPEG", quality=92)
    with toast_friendly_pdf(src) as out:
        assert _page_sizes(out) == [LETTER]
        image, raw, (x, y, w, h) = _page_picture(out)
    # Raised to MIN_DPI only (8 in wide at 200 dpi), never to 300.
    assert image.width == round(8 * toast_pdf.MIN_DPI)
    assert image.mode == "RGB"
    # Drawn exactly where the 300 dpi layout puts it (the OCR layer's spot).
    w300, h300, left300, top300 = toast_pdf._placement((1320, 688), toast_pdf.DPI)
    pt = 72 / toast_pdf.DPI
    assert x == pytest.approx(left300 * pt, abs=0.05)
    assert y == pytest.approx((toast_pdf.LETTER_PX[1] - top300 - h300) * pt, abs=0.05)
    assert w == pytest.approx(w300 * pt, abs=0.05)
    assert h == pytest.approx(h300 * pt, abs=0.05)


def test_full_resolution_page_keeps_its_pixels_in_colour_at_q85(tmp_path, monkeypatch):
    monkeypatch.setattr(toast_pdf, "find_tesseract", lambda: None)
    big = _text_image(["INVOICE 77", "TOTAL 1,234.56"], size=(2400, 3200))
    # Make it a photo upload (not a sane PDF) so it is converted.
    src = tmp_path / "scan.png"
    big.save(src)
    with toast_friendly_pdf(src) as out:
        image, raw, _ = _page_picture(out)
    # 2400x3200 is ~305 dpi on the page: stored at 300 dpi, i.e. at
    # (almost) its own size - not shrunk, not grown.
    w300, h300, _, _ = toast_pdf._placement(big.size, toast_pdf.DPI)
    assert (image.width, image.height) == (w300, h300)
    assert image.mode == "RGB"
    assert _jpeg_quality(raw) >= 83


def test_tall_receipt_chunks_stay_at_their_own_resolution(tmp_path, monkeypatch):
    monkeypatch.setattr(toast_pdf, "find_tesseract", lambda: None)
    img, _ = _receipt(width=1600, lines=120)  # ~295 dpi across 8 in
    src = _image_pdf(tmp_path / "tall.pdf", img)
    with toast_friendly_pdf(src) as out:
        sizes = _page_sizes(out)
        pictures = [_page_picture(out, i)[0] for i in range(len(sizes))]
    assert all(s == LETTER for s in sizes)
    assert {p.width for p in pictures} == {1600}  # the receipt's own width
    assert len(pictures) > 1


def test_over_budget_gives_up_quality_before_colour(tmp_path, monkeypatch):
    monkeypatch.setattr(toast_pdf, "find_tesseract", lambda: None)
    noisy = Image.effect_noise((1200, 1600), 60).convert("RGB")
    src = tmp_path / "noisy.png"
    noisy.save(src)
    with toast_friendly_pdf(src) as out:
        first = out.stat().st_size
        assert _page_picture(out)[0].mode == "RGB"
    # Just under the first encoding: the next one is still colour.
    monkeypatch.setattr(toast_pdf, "MAX_OUTPUT_BYTES", first - 1)
    with toast_friendly_pdf(src) as out:
        assert out.stat().st_size < first
        assert _page_picture(out)[0].mode == "RGB"
    assert [e[0] for e in toast_pdf.ENCODINGS[:3]] == ["RGB", "RGB", "RGB"]


def test_page_dpi_rules():
    assert toast_pdf.native_dpi((2400, 3150)) == pytest.approx(300)
    assert toast_pdf._page_dpi((2400, 3150), 300) == pytest.approx(300)
    assert toast_pdf._page_dpi((4800, 6300), 300) == 300  # capped
    assert toast_pdf._page_dpi((800, 1050), 300) == toast_pdf.MIN_DPI  # floor
    assert toast_pdf._page_dpi((800, 1050), 150) == 150  # never past the cap
    assert toast_pdf._page_dpi((2000, 2625), 300) == pytest.approx(250)


# ------------------------------------------------------------ email wiring


class _FakeSMTP:
    sent = []

    def __init__(self, *_a, **_k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def login(self, *_a):
        pass

    def sendmail(self, from_addr, to_addrs, msg):
        _FakeSMTP.sent.append((from_addr, to_addrs, msg))


@pytest.fixture
def fake_mail(monkeypatch):
    _FakeSMTP.sent = []
    monkeypatch.setattr(toast_email.smtplib, "SMTP_SSL", _FakeSMTP)
    monkeypatch.setattr(
        toast_email,
        "get_settings",
        lambda: types.SimpleNamespace(
            gmail_address="test@example.invalid",
            gmail_app_password="x",
            toast_invoice_email="toast@example.invalid",
        ),
    )
    monkeypatch.setattr(toast_pdf, "find_tesseract", lambda: None)
    return _FakeSMTP.sent


def _attachment(raw):
    msg = email.message_from_string(raw)
    parts = [p for p in msg.walk() if p.get_content_disposition() == "attachment"]
    assert len(parts) == 1
    return parts[0].get_filename(), parts[0].get_payload(decode=True)


def test_toast_email_attaches_the_converted_pdf(tmp_path, fake_mail):
    img, _ = _receipt()
    src = _image_pdf(tmp_path / "app.pdf", img)
    before = _sha(src)

    toast_email.send_toast_invoice_email("Vendor", "invoice", 12.5, "staff", src, "receipt.pdf")

    assert len(fake_mail) == 1
    _, to, raw = fake_mail[0]
    assert to == ["toast@example.invalid"]
    name, data = _attachment(raw)
    assert name == "receipt.pdf"
    sizes = _page_sizes(data)
    assert len(sizes) > 1 and all(s == LETTER for s in sizes)
    assert _sha(src) == before


def test_toast_email_sends_normal_pdf_unchanged(tmp_path, fake_mail):
    src = _image_pdf(tmp_path / "cam.pdf", Image.new("RGB", (1700, 2200), "white"), resolution=200.0)
    toast_email.send_toast_invoice_email("Vendor", "invoice", None, None, src, "cam.pdf")
    _, data = _attachment(fake_mail[0][2])
    assert data == src.read_bytes()


def test_toast_email_photo_uses_original_image(tmp_path, fake_mail):
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (1320, 688), "white").save(photo, "JPEG")
    stored_pdf = tmp_path / "p.pdf"
    Image.open(photo).save(stored_pdf, "PDF")  # what file_convert stores (1320x688pt page)
    toast_email.send_toast_invoice_email("V", "invoice", 1.0, None, stored_pdf, "p.pdf", source_path=photo)
    _, data = _attachment(fake_mail[0][2])
    assert _page_sizes(data) == [LETTER]


def test_toast_email_photo_conversion_failure_attaches_stored_pdf(tmp_path, fake_mail, monkeypatch):
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (100, 100), "white").save(photo, "JPEG")
    stored_pdf = tmp_path / "p.pdf"
    Image.open(photo).save(stored_pdf, "PDF")

    def boom(*_a, **_k):
        raise RuntimeError("nope")

    monkeypatch.setattr(toast_pdf, "build_toast_pdf", boom)
    toast_email.send_toast_invoice_email("V", "invoice", 1.0, None, stored_pdf, "p.pdf", source_path=photo)
    _, data = _attachment(fake_mail[0][2])
    assert data == stored_pdf.read_bytes()  # never the raw JPEG under a .pdf name


def test_attempt_toast_email_passes_photo_source(db, tmp_path, monkeypatch):
    from app.models import InvoiceSubmission
    from app.services import invoice_submission

    calls = []
    monkeypatch.setattr(invoice_submission, "send_toast_invoice_email", lambda **kw: calls.append(kw))
    Image.new("RGB", (50, 50), "white").save(tmp_path / "tok.jpg", "JPEG")
    Image.new("RGB", (50, 50), "white").save(tmp_path / "tok2.pdf", "PDF")
    photo = InvoiceSubmission(
        vendor="V", submission_type="invoice", original_filename="inv.jpg", stored_filename="tok.jpg",
        file_token="tok", pdf_filename=None,
    )
    pdf = InvoiceSubmission(
        vendor="V", submission_type="invoice", original_filename="inv.pdf", stored_filename="tok2.pdf",
        file_token="tok2", pdf_filename="tok2.pdf",
    )
    db.add_all([photo, pdf])
    db.commit()

    invoice_submission._attempt_toast_email(db, photo, tmp_path)
    invoice_submission._attempt_toast_email(db, pdf, tmp_path)

    assert calls[0]["source_path"] == tmp_path / "tok.jpg"
    assert calls[0]["pdf_path"] == tmp_path / "tok.pdf"
    assert calls[1]["source_path"] is None
    assert photo.toast_email_sent and pdf.toast_email_sent
