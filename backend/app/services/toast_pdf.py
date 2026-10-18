"""Builds a "Toast-friendly" copy of an invoice file for the Toast email.

Evidence from the production uploads folder: Toast's email-to-bill parser
read Adobe Scan PDFs (text layer), CamScanner PDFs (image-only, one JPEG per
612x792 page) and normal-shaped app scans (image-only, 595x806) fine. The
app scans it missed all had odd page geometry: one 595x7304pt page (12x
taller than wide), 595x3533/3200pt pages, or mixed 595x785/1197/637 pages -
a long receipt stretched onto one page. So the main fix is page shape; an
OCR text layer is added on top as a bonus.

`toast_friendly_pdf(src)` works on the stored file (PDF or photo):

1. A PDF whose pages all have a normal paper shape (aspect <= 1.5, at most
   17in a side) is passed through unchanged.
2. Otherwise each page's main embedded image is pulled out (the biggest
   one - CamScanner adds a small logo image to every page), or a photo is
   opened directly (JPEG/PNG/HEIC/...).
3. Very tall images are cut into letter-shaped chunks in the gaps between
   text lines (a small overlap is added only when no gap is near the cut,
   so a line of text is never lost between pages).
4. Each chunk goes on a portrait US Letter page (612x792pt), fitted inside
   a small margin, as one JPEG per page - the same plain structure
   CamScanner produces. The JPEG keeps the chunk's own pixels (only scaled
   DOWN, to at most 300 dpi on the page; a very small photo is scaled up to
   MIN_DPI). Tesseract (local) adds an invisible text layer to each page
   when it's installed; if it's missing or fails, the Letter pages are
   still sent without text. The PDF is re-encoded smaller (ENCODINGS, in
   order) only if it would go over MAX_OUTPUT_BYTES.

   (Until Oct 2026 every chunk was first scaled UP to a full 300 dpi Letter
   canvas, white margins included, at JPEG 80: a 1000 px receipt became a
   2550x3300 JPEG of interpolated pixels, which pushed real invoices over
   the size budget into the grayscale / 200 / 150 dpi fallbacks.)

Any other failure (unreadable image, a PDF with no extractable images, ...)
is logged and the original file is sent as-is. This must never block or
break the Toast email. The stored original is never modified; the converted
copy lives in a temp dir that the context manager removes afterwards.
"""

import contextlib
import io
import logging
import math
import pathlib
import shutil
import tempfile
import time
from typing import Iterator, List, Optional, Tuple

from PIL import Image, ImageOps

# Importing file_convert registers pillow-heif's HEIC opener with Pillow.
from app.services.file_convert import SUPPORTED_IMAGE_SUFFIXES

logger = logging.getLogger(__name__)

# Letter is 11/8.5 = 1.294 tall; taller images are cut into chunks this shape.
LETTER_ASPECT = 11.0 / 8.5
# A page up to this aspect (either orientation) is "sane". A4 is 1.414; a
# normal-shaped app scan Toast parsed was 1.4. The pages it missed were 2.0+.
MAX_SANE_ASPECT = 1.5
# Pages bigger than tabloid (17in) on either side aren't sane either.
MAX_SANE_SIDE_PT = 17 * 72
DPI = 300
LETTER_PX = (int(8.5 * DPI), int(11 * DPI))  # (2550, 3300)
# Images smaller than this share of the page's biggest image are logos /
# watermarks (CamScanner's 240x90 badge), not the invoice.
MIN_IMAGE_SHARE = 0.10
MARGIN_IN = 0.25
MAX_OUTPUT_BYTES = 5 * 1024 * 1024
OCR_TIMEOUT_SECONDS = 90
# After this long in total, remaining pages go out without a text layer
# (a long receipt can be 10+ pages; the web page polls for the send result
# for ~135s, so stay well inside that).
OCR_TOTAL_BUDGET_SECONDS = 60
# (mode, jpeg quality, max dpi) tried in order until the PDF fits the budget.
# A page is never stored above its own resolution (see _page_dpi), so the
# first one fits almost always; colour is given up only after quality.
ENCODINGS = [
    ("RGB", 85, 300),
    ("RGB", 75, 300),
    ("RGB", 70, 250),
    ("L", 70, 250),
    ("L", 60, 200),
    ("L", 50, 150),
]
# A photo smaller than this on the page is scaled up to it (an OCR reader's
# usual minimum), never further.
MIN_DPI = 200

TESSERACT_CANDIDATES = ["/opt/homebrew/bin/tesseract", "/usr/local/bin/tesseract", "/usr/bin/tesseract"]


class ToastPdfError(Exception):
    pass


def find_tesseract() -> Optional[str]:
    # launchd's PATH doesn't include Homebrew, so check the usual install
    # locations too, not just PATH.
    found = shutil.which("tesseract")
    if found:
        return found
    for cand in TESSERACT_CANDIDATES:
        if pathlib.Path(cand).exists():
            return cand
    return None


# ---------------------------------------------------------------- inspection


def _page_is_sane(width_pt: float, height_pt: float) -> bool:
    if width_pt <= 0 or height_pt <= 0:
        return False
    aspect = max(width_pt, height_pt) / min(width_pt, height_pt)
    return aspect <= MAX_SANE_ASPECT and max(width_pt, height_pt) <= MAX_SANE_SIDE_PT


def pdf_needs_no_changes(path: pathlib.Path) -> bool:
    """True when every page already has a normal paper shape (Letter/A4-like,
    at most 17in on a side). Toast parsed those fine whether or not they had
    a text layer: Adobe Scan (text layer) and CamScanner / normal-shaped app
    scans (image-only, 612x792 or 595x806). The ones it missed all had odd
    page geometry (595x7304pt, 595x3533pt, mixed 595x785/1197/637)."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if len(reader.pages) == 0:
        return False
    for page in reader.pages:
        box = page.mediabox
        width, height = float(box.width), float(box.height)
        if int(page.get("/Rotate") or 0) % 180:
            width, height = height, width
        if not _page_is_sane(width, height):
            return False
    return True


def pdf_text_chars(path: pathlib.Path) -> int:
    from pypdf import PdfReader

    return sum(len((p.extract_text() or "").strip()) for p in PdfReader(str(path)).pages)


# ------------------------------------------------------------ image sources


def _flatten(img: Image.Image) -> Image.Image:
    """Any mode -> RGB on a white background."""
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[-1])
        return bg
    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def _images_from_photo(path: pathlib.Path) -> List[Image.Image]:
    img = Image.open(path)
    out = []
    frames = getattr(img, "n_frames", 1)
    for i in range(frames):
        if frames > 1:
            img.seek(i)
        frame = ImageOps.exif_transpose(img) or img
        out.append(_flatten(frame.copy()))
    return out


def _images_from_pdf(path: pathlib.Path) -> List[Image.Image]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    out = []
    for page in reader.pages:
        imgs = []
        for file_img in page.images:
            try:
                imgs.append(file_img.image)
            except Exception:  # noqa: BLE001 - one odd image shouldn't sink the page
                logger.warning("toast_pdf: couldn't decode an image on a page of %s", path.name, exc_info=True)
        imgs = [i for i in imgs if i is not None and i.width > 0 and i.height > 0]
        if not imgs:
            continue
        biggest = max(imgs, key=lambda i: i.width * i.height)
        img = _flatten(biggest)
        rotate = int(page.get("/Rotate") or 0) % 360
        if rotate:
            # /Rotate turns the page clockwise; PIL rotates counter-clockwise.
            img = img.rotate(-rotate, expand=True)
        # A page with several big images (not just a logo) is unusual - only
        # the biggest is used; say so in the log.
        others = [i for i in imgs if i is not biggest and i.width * i.height >= MIN_IMAGE_SHARE * biggest.width * biggest.height]
        if others:
            logger.info("toast_pdf: page of %s has %d extra large image(s); using the biggest", path.name, len(others))
        out.append(img)
    return out


# ----------------------------------------------------------------- splitting


def _ink_per_row(gray: Image.Image) -> List[int]:
    """How many 'ink' pixels each row has. Ink = clearly darker than the
    paper, judged against the image's own brightness (photos of paper are
    rarely pure white)."""
    hist = gray.histogram()
    total = sum(hist)

    def percentile(share: float) -> int:
        acc = 0
        for value in range(256):
            acc += hist[value]
            if acc >= share * total:
                return value
        return 255

    # Paper ~ the 90th percentile brightness, ink ~ the 2nd. Halfway
    # between works for crisp scans and for blurry, grey phone photos.
    paper, dark = percentile(0.9), percentile(0.02)
    threshold = paper - max(20, (paper - dark) // 2)
    w, h = gray.size
    data = gray.tobytes()
    return [sum(1 for v in data[y * w:(y + 1) * w] if v < threshold) for y in range(h)]


def _find_cut(ink: List[int], low: int, target: int, width: int) -> Tuple[int, bool]:
    """Best row to cut on between `low` and `target`: the middle of the gap
    between text lines nearest to `target`.

    A gap row has (almost) as little ink as the emptiest row in the window -
    not necessarily zero, because a table's vertical rules or photo noise
    run through every row. If even the emptiest row is heavily inked (> 8%
    of the width), there's no gap. Returns (row, clean); clean=False means no
    gap was found and the least-inked row was used instead.
    """
    window = range(low, target + 1)
    floor = min(ink[r] for r in window)
    fallback = min(window, key=lambda r: (ink[r], -r))
    if floor > width * 0.08:
        return fallback, False
    limit = floor + max(1, int(width * 0.01))
    y = target
    while y >= low:
        if ink[y] <= limit:
            end = y
            while y - 1 >= low and ink[y - 1] <= limit:
                y -= 1
            if end - y >= 1:  # at least 2 rows: a real gap, not a stroke gap
                return (y + end + 1) // 2, True
        y -= 1
    return fallback, False


def split_tall_image(img: Image.Image) -> List[Image.Image]:
    """Cut an image taller than MAX_SANE_ASPECT into Letter-shaped chunks.

    Each cut goes in the middle of a gap between text lines (a run of rows
    with no ink) in the lower 40% of the chunk, the gap nearest the
    letter-height target. If there is no gap there, the cut goes on the
    least-inked row and the next chunk starts a little higher (overlap ~2.5%
    of the width, about a text line), so a line split by the cut is still
    whole on one page.
    """
    w, h = img.size
    if h <= w * MAX_SANE_ASPECT:
        return [img]
    chunk_h = max(int(round(w * LETTER_ASPECT)), 1)
    ink = _ink_per_row(img.convert("L"))
    overlap = max(2, int(round(w * 0.025)))

    chunks = []
    top = 0
    while top < h:
        remaining = h - top
        if remaining <= chunk_h * 1.15:
            # The remainder fits (at most 15% over): keep it whole.
            chunks.append(img.crop((0, top, w, h)))
            break
        # Spread what's left evenly over the pages it needs, so the last
        # page isn't a thin sliver.
        step = remaining / math.ceil(remaining / chunk_h)
        cut, clean = _find_cut(ink, top + int(step * 0.6), top + int(step), w)
        chunks.append(img.crop((0, top, w, cut)))
        top = cut if clean else max(cut - overlap, top + 1)
    return chunks


# -------------------------------------------------------------- page layout


def _placement(size: Tuple[int, int], dpi: int) -> Tuple[int, int, int, int]:
    """Where an image of `size` goes on a portrait Letter page at `dpi`:
    scaled (up or down) to fit inside a MARGIN_IN margin, centred. Returns
    (width, height, left, top) in pixels. Always portrait - a landscape
    image is scaled down to the page width rather than rotated, so its
    reading direction is kept."""
    pw, ph = int(8.5 * dpi), int(11 * dpi)
    margin = int(MARGIN_IN * dpi)
    scale = min((pw - 2 * margin) / size[0], (ph - 2 * margin) / size[1])
    w, h = max(1, int(round(size[0] * scale))), max(1, int(round(size[1] * scale)))
    return w, h, (pw - w) // 2, (ph - h) // 2


def scale_for_letter(img: Image.Image, dpi: int = DPI) -> Image.Image:
    w, h, _, _ = _placement(img.size, dpi)
    return img if (w, h) == img.size else img.resize((w, h), Image.LANCZOS)


def fit_to_letter(img: Image.Image, dpi: int = DPI) -> Image.Image:
    """The full Letter page at `dpi`: white around the scaled image."""
    _, _, left, top = _placement(img.size, dpi)
    canvas = Image.new("RGB", (int(8.5 * dpi), int(11 * dpi)), "white")
    canvas.paste(scale_for_letter(img, dpi), (left, top))
    return canvas


def _ocr_text_layer(scaled: Image.Image, tesseract_cmd: str, workdir: pathlib.Path, n: int) -> bytes:
    """Invisible-text-only PDF page the size of `scaled` at DPI.

    OCR runs on the scaled image itself, not the padded Letter canvas: a
    photo's grey paper next to a pure white margin makes Tesseract's global
    threshold turn the whole paper black (measured: 0 characters on a
    receipt chunk padded with white vs ~300 on the same chunk unpadded).
    """
    import pytesseract

    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    src = workdir / f"ocr_{n}.png"
    scaled.convert("L").save(src)
    return pytesseract.image_to_pdf_or_hocr(
        str(src),
        extension="pdf",
        config=f"--dpi {DPI} -c textonly_pdf=1",
        timeout=OCR_TIMEOUT_SECONDS,
    )


def native_dpi(size: Tuple[int, int]) -> float:
    """The dpi an image of `size` has when fitted (unscaled) inside the
    Letter page's margins."""
    usable_w, usable_h = 8.5 - 2 * MARGIN_IN, 11 - 2 * MARGIN_IN
    return max(size[0] / usable_w, size[1] / usable_h)


def _page_dpi(size: Tuple[int, int], max_dpi: int) -> float:
    """The resolution a chunk is stored at: its own, capped at `max_dpi`;
    a small photo is raised to MIN_DPI (never past `max_dpi`)."""
    return min(float(max_dpi), max(native_dpi(size), float(MIN_DPI)))


def _image_page(chunk: Image.Image, mode: str, quality: int, max_dpi: int) -> bytes:
    """A one-image Letter PDF page: the chunk fitted inside the margins,
    centred, as one JPEG at the chunk's own resolution (see _page_dpi).

    Pillow writes the JPEG as a page of exactly the picture's size; that
    page is then placed on a blank 612x792pt page, exactly where _placement
    puts the picture at DPI (which is where the OCR text layer goes too).
    The white margins cost nothing."""
    from pypdf import PageObject, PdfReader, PdfWriter, Transformation

    w300, h300, left300, top300 = _placement(chunk.size, DPI)
    dpi = _page_dpi(chunk.size, max_dpi)
    target = (max(1, int(round(w300 * dpi / DPI))), max(1, int(round(h300 * dpi / DPI))))
    img = chunk if target == chunk.size else chunk.resize(target, Image.LANCZOS)
    single = io.BytesIO()
    img.convert(mode).save(single, "PDF", resolution=72.0, quality=quality)
    picture = PdfReader(single).pages[0]  # 1pt per pixel
    pt = 72.0 / DPI
    page = PageObject.create_blank_page(width=612, height=792)
    page.merge_transformed_page(
        picture,
        Transformation()
        .scale(w300 * pt / target[0], h300 * pt / target[1])
        .translate(left300 * pt, (LETTER_PX[1] - top300 - h300) * pt),
    )
    writer = PdfWriter()
    writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# One output page: the source chunk (small - pages are rendered at Letter
# size only while assembling, to keep memory low) and its OCR text layer.
PageParts = Tuple[Image.Image, Optional[bytes]]


def _assemble(pages: List[PageParts], mode: str, quality: int, dpi: int) -> bytes:
    from pypdf import PdfReader, PdfWriter, Transformation

    pt = 72.0 / DPI
    writer = PdfWriter()
    for chunk, text_pdf in pages:
        img_page = PdfReader(io.BytesIO(_image_page(chunk, mode, quality, dpi))).pages[0]
        if text_pdf is not None:
            text_page = PdfReader(io.BytesIO(text_pdf)).pages[0]
            # The text layer is the size of the image as scaled for OCR (at
            # DPI); move it onto the image's spot (PDF y runs up from the
            # bottom).
            _, h, left, top = _placement(chunk.size, DPI)
            dx = left * pt
            dy = (LETTER_PX[1] - top - h) * pt
            img_page.merge_transformed_page(text_page, Transformation().translate(dx, dy))
        writer.add_page(img_page)
    writer.add_metadata({"/Producer": "Spice Town (Tesseract OCR for Toast)"})
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ------------------------------------------------------------------ main API


def build_toast_pdf(src: pathlib.Path, workdir: pathlib.Path) -> Optional[pathlib.Path]:
    """Write a searchable Letter PDF for `src` into `workdir` and return its
    path, or None when `src` should be sent as-is (already fine). Raises on
    any failure - `toast_friendly_pdf` turns that into a fallback."""
    suffix = src.suffix.lower()
    if suffix == ".pdf":
        if pdf_needs_no_changes(src):
            return None
        sources = _images_from_pdf(src)
    elif suffix in SUPPORTED_IMAGE_SUFFIXES:
        sources = _images_from_photo(src)
    else:
        raise ToastPdfError(f"not a PDF or a supported image: {suffix!r}")
    if not sources:
        raise ToastPdfError("no images found to OCR")

    # The text layer is a bonus: the Letter-shaped pages are the main fix,
    # so a missing Tesseract or a failed OCR still sends the normalized PDF.
    tesseract_cmd = find_tesseract()
    if tesseract_cmd is None:
        logger.warning("toast_pdf: tesseract not found; sending %s as Letter pages without a text layer", src.name)

    pages = []  # type: List[PageParts]
    n = 0
    ocr_deadline = time.monotonic() + OCR_TOTAL_BUDGET_SECONDS
    for source in sources:
        for chunk in split_tall_image(source):
            text_pdf = None
            if tesseract_cmd is not None and time.monotonic() < ocr_deadline:
                try:
                    text_pdf = _ocr_text_layer(scale_for_letter(chunk), tesseract_cmd, workdir, n)
                except Exception:  # noqa: BLE001
                    logger.warning("toast_pdf: OCR failed on page %d of %s", n + 1, src.name, exc_info=True)
            pages.append((chunk, text_pdf))
            n += 1

    data = b""
    for mode, quality, dpi in ENCODINGS:
        data = _assemble(pages, mode, quality, dpi)
        if len(data) <= MAX_OUTPUT_BYTES:
            break
    out = workdir / "toast.pdf"
    out.write_bytes(data)
    return out


@contextlib.contextmanager
def toast_friendly_pdf(src: pathlib.Path) -> Iterator[pathlib.Path]:
    """Yields the path to attach: a converted copy in a temp dir, or `src`
    itself when it's already fine or anything went wrong. The temp dir is
    removed on exit; `src` is never touched."""
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="toast-pdf-"))
    try:
        started = time.monotonic()
        result = None
        try:
            result = build_toast_pdf(src, workdir)
        except Exception:  # noqa: BLE001 - conversion must never block the Toast email
            logger.exception("toast_pdf: conversion failed for %s; sending the original", src.name)
        elapsed = time.monotonic() - started
        if result is None:
            logger.info("toast_pdf: sending %s unchanged (%.1fs)", src.name, elapsed)
            yield src
        else:
            logger.info(
                "toast_pdf: converted %s -> searchable PDF, %d bytes (%.1fs)",
                src.name,
                result.stat().st_size,
                elapsed,
            )
            yield result
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
