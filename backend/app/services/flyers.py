"""Marketing flyers library (docs/MARKETING_FLYERS_CONTRACT.md, sections 1-4).

A flyer is an image or a PDF plus an optional WhatsApp caption, kept in a
normal Finder folder on the Mac mini - FLYERS_DIR (default
~/Spice Town Flyers), one sub-folder per category:

    Spice Town Flyers/
        Groceries/  Street Kitchen/  Flowers/  Halal Meat/  Spice Town/  Others/
        _Deleted/<Category>/   (soft-deleted flyers, never removed outright)
        .thumbs/               (hidden thumbnail cache)

File names are built here, never from anything the client sent:
"<store date> <sanitized title> (<flyer id>).<ext>". Uploads are validated by
their content, not their name or declared type:
- images (JPEG, PNG, WebP, HEIC/HEIF; macOS `sips` converts HEIC if
  pillow-heif can't) are re-encoded as one canonical JPEG: EXIF orientation
  applied, colours converted to sRGB, all metadata dropped, at most 2560 px
  on the longest side and about 5 MB (what WhatsApp handles well);
- PDFs must start with %PDF, open in pypdf and be at most 20 MB; stored as
  uploaded, with a first-page thumbnail rendered by macOS (`sips`, then
  `qlmanage`) when possible and a generic PDF icon otherwise.

Sending lives in services/flyer_sender.py; this module creates the send rows.
Only WhatsApp groups an admin marked Marketing can be targeted
(whatsapp_groups.marketing_targets).
"""

import asyncio
import datetime as dt
import hashlib
import io
import logging
import os
import pathlib
import re
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
import uuid
import zlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException, Request
from PIL import Image, ImageCms, ImageDraw, ImageFont, ImageOps
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Flyer, FlyerSend, User
from app.services import whatsapp_groups
from app.team.media import MAX_PIXELS
from app.timeutil import to_restaurant_date, utcnow

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:  # pragma: no cover - pillow-heif is in requirements.txt
    HEIC_SUPPORTED = False

logger = logging.getLogger(__name__)

FEATURE = "marketing_flyers"

# (key, label, folder) - the fixed list, in this order (contract section 2).
CATEGORIES = (
    ("groceries", "Groceries", "Groceries"),
    ("street_kitchen", "Street Kitchen", "Street Kitchen"),
    ("flowers", "Flowers", "Flowers"),
    ("halal_meat", "Halal Meat", "Halal Meat"),
    ("spice_town", "Spice Town (Generic)", "Spice Town"),
    ("others", "Others", "Others"),
)
CATEGORY_LABELS = {key: label for key, label, _ in CATEGORIES}
CATEGORY_FOLDERS = {key: folder for key, _, folder in CATEGORIES}

MB = 1024 * 1024
MAX_IMAGE_MB = 25
MAX_PDF_MB = 20
MAX_IMAGE_BYTES = MAX_IMAGE_MB * MB
MAX_PDF_BYTES = MAX_PDF_MB * MB
# Whole-request cap checked from Content-Length before the multipart body is
# read: the biggest file plus the form fields and multipart framing.
MAX_REQUEST_BYTES = MAX_IMAGE_BYTES + MB
ACCEPT = ["image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "application/pdf"]

# Stored image: one JPEG WhatsApp sends without fuss.
MAX_SIDE = 2560
JPEG_QUALITY = 88
TARGET_MAX_BYTES = 5 * MB
THUMB_SIDE = 480
THUMB_QUALITY = 80
# MPO = a JPEG with extra frames (iPhone HDR / Android Ultra HDR photos).
IMAGE_FORMATS = {"JPEG", "MPO", "PNG", "WEBP", "HEIF"}

MAX_TITLE = 120
MAX_CAPTION = 1000
FILE_TITLE_CHARS = 80
# APFS allows 255 bytes per name; the date, id and extension need < 30.
FILE_TITLE_BYTES = 160
MAX_ORIGINAL_NAME = 200
MAX_GROUPS_PER_SEND = 25
MAX_IDEMPOTENCY_KEY = 100
# SQLite's largest INTEGER: a bigger id in a URL is simply "not found" (the
# driver would raise OverflowError -> 500).
MAX_ROW_ID = 2**63 - 1
IDEMPOTENCY_WINDOW = dt.timedelta(hours=24)
DEFAULT_LIMIT = 30
MAX_LIMIT = 100

DELETED_DIR = "_Deleted"
THUMBS_DIR = ".thumbs"
# After a failed PDF render the generic icon is served; rendering is tried
# again after this long (a one-off failure shouldn't stick forever).
THUMB_RETRY_AFTER_SECONDS = 24 * 3600
RENDER_TIMEOUT_SECONDS = 20

QUEUED, SENDING, SENT, FAILED = "queued", "sending", "sent", "failed"
PENDING = (QUEUED, SENDING)
CHANNEL_WHATSAPP = "whatsapp"
NOT_CONNECTED = whatsapp_groups.NOT_CONNECTED

UNSUPPORTED = "That file type isn't supported. Upload a JPEG, PNG, WebP or HEIC photo, or a PDF."
CORRUPT_IMAGE = "That image looks damaged - it can't be opened. Try exporting it again."
CORRUPT_PDF = "That PDF looks damaged - it can't be opened. Try exporting it again."
PDF_TOO_COMPLEX = "That PDF can't be used - it unpacks to far too much data. Try exporting it again."
TOO_BIG = f"That file is too big. Images can be up to {MAX_IMAGE_MB} MB and PDFs up to {MAX_PDF_MB} MB."
TOO_MANY_PIXELS = "That image is too large (too many pixels). Resize it and try again."

# A decoded image can be many times its file size (50 MP RGBA ~ 200 MB):
# flyers are rare, so one at a time.
_PROCESSING_SLOTS = threading.BoundedSemaphore(1)
# File moves (rename / category change / delete) and send-batch creation
# (idempotency check + insert) are serialized within the process.
_FILE_LOCK = threading.Lock()
_SEND_LOCK = threading.Lock()
_CHUNK = 256 * 1024


class FlyerError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# --- Folders ---------------------------------------------------------------------------


def base_dir() -> pathlib.Path:
    raw = (get_settings().flyers_dir or "").strip()
    path = pathlib.Path(raw).expanduser() if raw else pathlib.Path.home() / "Spice Town Flyers"
    return path.resolve()


def ensure_dirs() -> pathlib.Path:
    """The base folder and the six category folders (created on first use)."""
    base = base_dir()
    for _, _, folder in CATEGORIES:
        (base / folder).mkdir(parents=True, exist_ok=True)
    return base


def _inside(base: pathlib.Path, path: pathlib.Path) -> bool:
    return path == base or base in path.parents


def flyer_file(flyer: Flyer) -> Optional[pathlib.Path]:
    """The flyer's file on disk, or None when it's missing (or its stored
    path would point outside the flyers folder - never served). The folder
    is a normal Finder folder, so someone may rename a file or move it to
    another category folder by hand: the "(<id>)" in its name still finds
    it."""
    base = base_dir()
    if flyer.file_rel_path:
        path = (base / flyer.file_rel_path).resolve()
        if _inside(base, path) and path != base and path.is_file():
            return path
    return _find_by_id(base, flyer)


def _find_by_id(base: pathlib.Path, flyer: Flyer) -> Optional[pathlib.Path]:
    """The one file named "... (<id>).<ext>" (or "(<id>-n)") in the flyer's
    own folders - the category folders for a live flyer, _Deleted's for a
    deleted one. None when there's no such file, or more than one."""
    if not flyer.id:
        return None
    ext = extension_for(flyer.mime)
    parents = [base / DELETED_DIR / f for _, _, f in CATEGORIES] if flyer.deleted_at else [base / f for _, _, f in CATEGORIES]
    pattern = re.compile(rf" \({flyer.id}(-\d+)?\)\.{ext}$", re.IGNORECASE)
    found = []
    for parent in parents:
        if not parent.is_dir():
            continue
        found.extend(p for p in parent.iterdir() if pattern.search(p.name) and p.is_file())
    if len(found) != 1:
        return None
    path = found[0].resolve()
    return path if _inside(base, path) else None


# --- Names -----------------------------------------------------------------------------

_FILE_UNSAFE = re.compile(r'[/\\:*?"<>|]')
# Bidi embeddings / overrides / isolates (they can make text - or a file name -
# read backwards) and the BOM: never kept.
_BIDI_CONTROLS = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff")
# Zero-width (non-)joiners build emoji (a cook, a family) and Indian-script
# conjuncts - kept even in file names.
_JOINERS = frozenset("\u200c\u200d")


def _drop_control(text: str, keep: str = "") -> str:
    """Drops control characters (except those in `keep`; other line breaks
    and tabs become a space), lone surrogates and bidi controls. Everything
    else stays: zero-width joiners (emoji ZWJ sequences, Indian scripts) and
    characters newer than this Python's Unicode tables (category "Cn" - e.g.
    the newest emoji) must reach WhatsApp intact."""
    out = []
    for ch in text:
        if ch in keep:
            out.append(ch)
            continue
        category = unicodedata.category(ch)
        if category == "Cc":
            if ch.isspace():
                out.append(" ")
        elif category != "Cs" and ch not in _BIDI_CONTROLS:
            out.append(ch)
    return "".join(out)


def _file_name_chars(text: str) -> str:
    """For a Finder name, also no invisible format characters (other than the
    joiners) and no Unicode noncharacters."""
    return "".join(
        ch for ch in text
        if (unicodedata.category(ch) != "Cf" or ch in _JOINERS)
        and not (0xFDD0 <= ord(ch) <= 0xFDEF or (ord(ch) & 0xFFFE) == 0xFFFE)
    )


def _has_visible(text: str) -> bool:
    return any(not ch.isspace() and unicodedata.category(ch) != "Cf" for ch in text)


def safe_file_title(title: str) -> str:
    """The title as a Finder-safe name part: no path separators or control
    characters, no leading dots, at most FILE_TITLE_CHARS characters."""
    text = unicodedata.normalize("NFC", title or "")
    text = _FILE_UNSAFE.sub(" ", _file_name_chars(_drop_control(text)))
    text = " ".join(text.split()).strip(". ")
    text = text[:FILE_TITLE_CHARS]
    while len(text.encode("utf-8")) > FILE_TITLE_BYTES:
        text = text[:-1]
    text = text.strip(". ")
    return text if _has_visible(text) else "Flyer"


def extension_for(mime: str) -> str:
    return "pdf" if mime == "application/pdf" else "jpg"


def kind_for(mime: str) -> str:
    return "pdf" if mime == "application/pdf" else "image"


def display_file_name(flyer: Flyer) -> str:
    """For people (downloads, the WhatsApp document name) - not the path."""
    return f"{safe_file_title(flyer.title)}.{extension_for(flyer.mime)}"


def stored_file_name(flyer_id: int, title: str, created_at: dt.datetime, mime: str) -> str:
    day = to_restaurant_date(created_at).isoformat()
    return f"{day} {safe_file_title(title)} ({flyer_id}).{extension_for(mime)}"


def _candidates(name: str):
    """name, then "<stem>-2.<ext>", "-3"... - a file already there is never
    overwritten (e.g. one left from a database restored from backup)."""
    yield name
    stem, dot, ext = name.rpartition(".")
    for n in range(2, 100):
        yield f"{stem[:-1]}-{n}).{ext}" if stem.endswith(")") else f"{stem}-{n}.{ext}"
    raise FlyerError(500, "Couldn't find a free file name in the Flyers folder.")


def _place_new(folder: pathlib.Path, name: str, data: bytes) -> pathlib.Path:
    """Writes `data` as a new file in `folder` (temp file + fsync + hard
    link, so a reader never sees a partial file and nothing is overwritten)."""
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".upload-{secrets.token_hex(6)}.tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        for candidate in _candidates(name):
            dest = folder / candidate
            try:
                os.link(tmp, dest)
                return dest
            except FileExistsError:
                continue
        raise FlyerError(500, "Couldn't save the file.")  # pragma: no cover - _candidates raises first
    finally:
        tmp.unlink(missing_ok=True)


def _move(src: pathlib.Path, folder: pathlib.Path, name: str) -> pathlib.Path:
    """Moves a file to folder/name without overwriting anything there."""
    folder.mkdir(parents=True, exist_ok=True)
    for candidate in _candidates(name):
        dest = folder / candidate
        if dest == src:
            return dest
        try:
            os.link(src, dest)
        except FileExistsError:
            if os.path.samefile(src, dest):
                # The same file under another case ("diwali" -> "Diwali") on
                # a case-insensitive disk: just change the name's case.
                os.rename(src, dest)
                return dest
            continue
        src.unlink()
        return dest
    raise FlyerError(500, "Couldn't move the file.")  # pragma: no cover


def _rel(path: pathlib.Path) -> str:
    return path.relative_to(base_dir()).as_posix()


# --- Field validation ------------------------------------------------------------------


def clean_title(value) -> str:
    if not isinstance(value, str):
        raise FlyerError(400, "Give the flyer a title.")
    title = " ".join(_drop_control(unicodedata.normalize("NFC", value)).split())
    if not _has_visible(title):
        raise FlyerError(400, "Give the flyer a title.")
    if len(title) > MAX_TITLE:
        raise FlyerError(400, f"The title can be at most {MAX_TITLE} characters.")
    return title


def clean_category(value) -> str:
    if not isinstance(value, str) or value.strip() not in CATEGORY_LABELS:
        raise FlyerError(400, "Choose a category: " + ", ".join(label for _, label, _ in CATEGORIES) + ".")
    return value.strip()


def clean_caption(value) -> Optional[str]:
    """The WhatsApp message under the flyer: newlines kept (WhatsApp
    formatting), other control characters dropped; blank = none."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise FlyerError(400, "The message must be text.")
    text = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    text = _drop_control(text, keep="\n\t").strip()
    if not _has_visible(text):
        return None
    if len(text) > MAX_CAPTION:
        raise FlyerError(400, f"The message can be at most {MAX_CAPTION:,} characters.")
    return text


def clean_original_name(value) -> Optional[str]:
    """The uploaded file's own name, for display only (never used in a path)."""
    if not isinstance(value, str):
        return None
    name = value.replace("\\", "/").rsplit("/", 1)[-1]
    name = " ".join(_drop_control(name).split())[:MAX_ORIGINAL_NAME]
    return name or None


# --- Upload processing -----------------------------------------------------------------


@dataclass
class Processed:
    data: bytes
    mime: str
    width: Optional[int]
    height: Optional[int]


def check_request_size(request: Request) -> None:
    """Refuses an upload by its Content-Length before the body is read (as
    app/team/media.py does), so a huge body is never spooled to disk."""
    raw = request.headers.get("content-length")
    if raw is None:
        raise HTTPException(status_code=411, detail="Upload needs a Content-Length.")
    try:
        length = int(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad Content-Length.")
    if length > MAX_REQUEST_BYTES:
        raise HTTPException(status_code=400, detail=TOO_BIG)


@dataclass
class UploadParts:
    data: Optional[bytes]  # the `file` part (None when there isn't one)
    filename: Optional[str]
    fields: Dict[str, str]


def parse_upload(content_type: Optional[str], body: bytes) -> UploadParts:
    """Parses the multipart body with Starlette's own parser - meant to run
    in a worker thread (it gets a private event loop), NOT on the server's
    event loop: python-multipart can take many seconds on a pathological
    body (a big file made mostly of characters from the boundary), and
    request.form() would freeze every other request meanwhile. One file
    part (`file`) at most; FlyerError(400) for a malformed body."""
    if not (content_type or "").lower().startswith("multipart/form-data"):
        return UploadParts(None, None, {})
    from starlette.datastructures import Headers, UploadFile
    from starlette.formparsers import MultiPartException, MultiPartParser

    async def stream():
        yield body

    async def parse():
        return await MultiPartParser(Headers({"content-type": content_type}), stream(), max_files=1, max_fields=10).parse()

    try:
        form = asyncio.run(parse())
    except MultiPartException as exc:
        raise FlyerError(400, exc.message)
    except Exception:  # noqa: BLE001 - e.g. no boundary in the header
        raise FlyerError(400, "Couldn't read the upload. Try again.")
    try:
        upload = form.get("file")
        data = filename = None
        if isinstance(upload, UploadFile):
            upload.file.seek(0)
            data = read_limited(upload.file)
            filename = upload.filename
        fields = {k: v for k, v in form.multi_items() if isinstance(v, str)}
        return UploadParts(data, filename, fields)
    finally:
        for _, value in form.multi_items():
            if isinstance(value, UploadFile):
                value.file.close()


def read_limited(stream, limit: int = MAX_IMAGE_BYTES) -> bytes:
    buf = bytearray()
    while True:
        chunk = stream.read(_CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > limit:
            raise FlyerError(400, TOO_BIG)
    return bytes(buf)


def _is_pdf(data: bytes) -> bool:
    # The spec allows a little junk before the header; readers look in the
    # first 1 KB.
    return b"%PDF-" in data[:1024]


_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs", b"mif1", b"msf1"}


def _is_heif(data: bytes) -> bool:
    return len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS


def _looks_like_accepted_image(data: bytes) -> bool:
    """Starts like a JPEG, PNG, WebP or HEIC file (so if it can't be read,
    it's damaged rather than the wrong type)."""
    return (
        data.startswith(b"\xff\xd8\xff")
        or data.startswith(b"\x89PNG\r\n\x1a\n")
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
        or _is_heif(data)
    )


def _sips_to_jpeg(data: bytes) -> Optional[bytes]:
    """macOS fallback for a HEIC photo Pillow can't read (pillow-heif
    missing or broken): `sips` converts it to JPEG. None if that fails."""
    sips = shutil.which("sips") or "/usr/bin/sips"
    if not os.path.exists(sips):
        return None
    with tempfile.TemporaryDirectory(prefix="flyer-heic-") as tmp:
        src, out = pathlib.Path(tmp) / "in.heic", pathlib.Path(tmp) / "out.jpg"
        src.write_bytes(data)
        try:
            subprocess.run(
                [sips, "-s", "format", "jpeg", str(src), "--out", str(out)],
                capture_output=True, timeout=RENDER_TIMEOUT_SECONDS, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.read_bytes() if out.is_file() and out.stat().st_size else None


def _to_srgb(img: Image.Image, icc: Optional[bytes]) -> Image.Image:
    """RGB in sRGB: an embedded colour profile (e.g. an iPhone's Display P3)
    is converted rather than just dropped, so colours don't wash out."""
    if icc and img.mode in ("RGB", "CMYK"):
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            return ImageCms.profileToProfile(img, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
        except Exception:  # noqa: BLE001 - a bad profile just means "assume sRGB"
            pass
    return img if img.mode == "RGB" else img.convert("RGB")


def _encode_jpeg(img: Image.Image) -> bytes:
    """JPEG at JPEG_QUALITY, stepping quality (then size) down until it is at
    most TARGET_MAX_BYTES."""
    for _round in range(6):
        for quality in (JPEG_QUALITY, 82, 76, 70):
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=quality, optimize=True)
            if out.tell() <= TARGET_MAX_BYTES:
                return out.getvalue()
        img = img.resize((max(1, int(img.width * 0.8)), max(1, int(img.height * 0.8))), Image.LANCZOS)
    return out.getvalue()


def _normalize_image(data: bytes) -> Processed:
    with Image.open(io.BytesIO(data)) as probe:
        if probe.format not in IMAGE_FORMATS:
            raise FlyerError(400, UNSUPPORTED)
        width, height = probe.size
        # The decompression-bomb guard: checked from the header, before
        # anything is decoded (team/media.py's MAX_PIXELS).
        if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
            raise FlyerError(400, TOO_MANY_PIXELS)
        probe.verify()
    # verify() leaves the image unusable - reopen to decode.
    with Image.open(io.BytesIO(data)) as img:
        if img.format in ("JPEG", "MPO"):
            # libjpeg decodes straight at 1/2, 1/4 or 1/8 scale when the
            # photo is much bigger than what's kept.
            img.draft("RGB", (MAX_SIDE, MAX_SIDE))
        img.load()
        icc = img.info.get("icc_profile")
        # Palette / 1-bit images resize badly (nearest neighbour only):
        # expand them first.
        if img.mode in ("P", "PA"):
            img = img.convert("RGBA" if img.mode == "PA" or "transparency" in img.info else "RGB")
        elif img.mode == "1":
            img = img.convert("L")
        elif img.mode.startswith("I;16") or img.mode in ("I", "F"):
            # 16-bit / float greyscale (some PNG exports): scale to 8 bits.
            img = img.convert("I").point(lambda v: v * (1 / 256)).convert("L")
        # Shrink first, in the image's own mode, so the conversions below
        # only touch the small copy.
        factor = max(img.size) // MAX_SIDE
        if factor >= 2 and img.mode not in ("RGBA", "LA"):
            try:
                img = img.reduce(factor)
            except ValueError:  # a mode reduce() doesn't support
                pass
        img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.split()[-1])
            img = flat
        img = _to_srgb(img, icc)
        # Nothing from the original rides along (EXIF/GPS, comments, ICC).
        img.info = {}
        jpeg = _encode_jpeg(img)
    with Image.open(io.BytesIO(jpeg)) as done:
        return Processed(jpeg, "image/jpeg", done.width, done.height)


# Opening a PDF and counting its pages makes pypdf inflate the
# cross-reference and object streams it needs - and a crafted PDF can make
# those inflate to many GB (a 400 KB file to 400 MB, a 20 MB one to ~20 GB):
# a decompression bomb, the PDF twin of MAX_PIXELS. While _check_pdf runs,
# pypdf's inflating is capped at PDF_CHECK_INFLATE_BYTES in total (legit
# PDFs need a few MB at most - page contents and images aren't touched) and
# the other expanding filters (LZW, RunLength - never used for those streams
# by real tools) are refused. The cap is per thread: pypdf used anywhere
# else (services/toast_pdf.py) is untouched.
PDF_CHECK_INFLATE_BYTES = 64 * MB
_pdf_check = threading.local()
_pdf_cap_lock = threading.Lock()


class _PdfTooBig(Exception):
    pass


def _pdf_over_budget() -> _PdfTooBig:
    _pdf_check.too_big = True
    _pdf_check.budget = 0
    return _PdfTooBig()


def _capped_inflate(data: bytes) -> bytes:
    """pypdf.filters.decompress for _check_pdf: the same fallbacks (zlib,
    then zlib/gzip auto-detect, then what inflates before any damage), but
    never more output than the remaining budget."""
    budget = _pdf_check.budget
    if getattr(_pdf_check, "too_big", False):
        raise _PdfTooBig()
    for wbits in (zlib.MAX_WBITS, zlib.MAX_WBITS | 32):
        inflater = zlib.decompressobj(wbits)
        try:
            out = inflater.decompress(data, budget + 1)
        except zlib.error:
            continue
        if len(out) > budget or inflater.unconsumed_tail:
            raise _pdf_over_budget()
        _pdf_check.budget = budget - len(out)
        return out
    inflater = zlib.decompressobj(zlib.MAX_WBITS | 32)
    out = bytearray()
    for start in range(0, len(data), 4096):
        piece = data[start:start + 4096]
        saved = inflater.copy()
        try:
            out += inflater.decompress(piece, budget + 1 - len(out))
            if inflater.unconsumed_tail:
                raise _pdf_over_budget()
        except zlib.error:
            # Byte by byte up to the damage (what pypdf keeps), then stop.
            inflater = saved
            for i in range(len(piece)):
                try:
                    out += inflater.decompress(piece[i:i + 1], budget + 1 - len(out))
                except zlib.error:
                    break
                if len(out) > budget or inflater.unconsumed_tail:
                    raise _pdf_over_budget()
            break
        if len(out) > budget:
            raise _pdf_over_budget()
    _pdf_check.budget = budget - len(out)
    return bytes(out)


def _install_pdf_inflate_cap() -> None:
    """Wraps pypdf.filters' stream decoding once, so it honours the cap above
    while (and only while) this thread is inside _check_pdf."""
    import pypdf.filters as pdf_filters

    with _pdf_cap_lock:
        if getattr(pdf_filters, "_flyers_inflate_cap", False):
            return
        original_decompress = pdf_filters.decompress
        original_decode = pdf_filters.decode_stream_data

        def decompress(data):
            if getattr(_pdf_check, "budget", None) is None:
                return original_decompress(data)
            return _capped_inflate(data)

        def decode_stream_data(stream):
            if getattr(_pdf_check, "budget", None) is not None:
                filters = stream.get("/Filter", ())
                filters = filters.get_object() if hasattr(filters, "get_object") else filters
                names = filters if isinstance(filters, (list, tuple)) else [filters]
                if any(str(n) in ("/LZWDecode", "/LZW", "/RunLengthDecode", "/RL") for n in names):
                    raise _pdf_over_budget()
            return original_decode(stream)

        pdf_filters.decompress = decompress
        pdf_filters.decode_stream_data = decode_stream_data
        pdf_filters._flyers_inflate_cap = True


def _check_pdf(data: bytes) -> None:
    if len(data) > MAX_PDF_BYTES:
        raise FlyerError(400, f"That PDF is too big. PDFs can be up to {MAX_PDF_MB} MB.")
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - pypdf is in requirements.txt
        return
    _install_pdf_inflate_cap()
    _pdf_check.budget, _pdf_check.too_big = PDF_CHECK_INFLATE_BYTES, False
    problem: Optional[FlyerError] = None
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        count_pages = True
        if reader.is_encrypted:
            try:
                opened = reader.decrypt("")
            except Exception:  # noqa: BLE001 - can't check (crypto backend) - take it as is
                opened, count_pages = True, False
            if not opened:
                problem = FlyerError(400, "That PDF is password-protected. Upload one that opens without a password.")
        if problem is None and count_pages and len(reader.pages) < 1:
            problem = FlyerError(400, CORRUPT_PDF)
    except Exception:  # noqa: BLE001 - anything pypdf can't read is damaged
        problem = FlyerError(400, CORRUPT_PDF)
    finally:
        too_big = _pdf_check.too_big
        _pdf_check.budget, _pdf_check.too_big = None, False
    # Checked last: pypdf may have swallowed the refusal and carried on
    # (non-strict mode repairs what it can) - the PDF is still refused.
    if too_big:
        raise FlyerError(400, PDF_TOO_COMPLEX)
    if problem is not None:
        raise problem


def process_upload(data: bytes) -> Processed:
    """Validates an upload by its content and returns what to store.
    Raises FlyerError(400) with a message for the person uploading."""
    if not data:
        raise FlyerError(400, "The file is empty.")
    if len(data) > MAX_IMAGE_BYTES:
        raise FlyerError(400, TOO_BIG)
    # An image is recognised by its own signature first, so a photo whose
    # metadata happens to contain "%PDF-" is still a photo.
    if not _looks_like_accepted_image(data) and _is_pdf(data):
        _check_pdf(data)
        return Processed(data, "application/pdf", None, None)
    with _PROCESSING_SLOTS:
        try:
            return _normalize_image(data)
        except FlyerError:
            raise
        except Image.DecompressionBombError:
            raise FlyerError(400, TOO_MANY_PIXELS)
        except Image.UnidentifiedImageError:
            if _is_heif(data):
                converted = _sips_to_jpeg(data)
                if converted:
                    try:
                        return _normalize_image(converted)
                    except FlyerError:
                        raise
                    except Exception:  # noqa: BLE001
                        pass
                raise FlyerError(400, CORRUPT_IMAGE)
            raise FlyerError(400, CORRUPT_IMAGE if _looks_like_accepted_image(data) else UNSUPPORTED)
        except Exception:  # noqa: BLE001 - a recognised image Pillow can't decode
            raise FlyerError(400, CORRUPT_IMAGE)


# --- Library -----------------------------------------------------------------------------


def create_flyer(db: Session, user: User, data: Optional[bytes], original_filename, title, category, caption) -> Flyer:
    """Validates the fields and the file, stores the file, commits the row."""
    if data is None:
        raise FlyerError(400, "Choose an image or a PDF to upload.")
    title = clean_title(title)
    category = clean_category(category)
    caption = clean_caption(caption)
    processed = process_upload(data)
    folder = ensure_dirs() / CATEGORY_FOLDERS[category]
    now = utcnow()
    flyer = Flyer(
        title=title,
        category=category,
        caption=caption,
        file_rel_path="",
        original_filename=clean_original_name(original_filename),
        mime=processed.mime,
        size_bytes=len(processed.data),
        sha256=hashlib.sha256(processed.data).hexdigest(),
        width=processed.width,
        height=processed.height,
        created_at=now,
        created_by_user_id=user.id,
        created_by_name=user.username,
    )
    db.add(flyer)
    db.flush()  # the id is part of the file name
    dest = None
    try:
        dest = _place_new(folder, stored_file_name(flyer.id, title, now, processed.mime), processed.data)
        flyer.file_rel_path = _rel(dest)
        db.commit()
    except Exception:
        db.rollback()
        if dest is not None:
            dest.unlink(missing_ok=True)
        raise
    db.refresh(flyer)
    logger.info("Flyer %s stored at %s (%d bytes) by %s", flyer.id, flyer.file_rel_path, flyer.size_bytes, user.username)
    return flyer


def _valid_row_id(value) -> bool:
    return isinstance(value, int) and 0 < value <= MAX_ROW_ID


def get_flyer(db: Session, flyer_id: int) -> Flyer:
    flyer = db.get(Flyer, flyer_id) if _valid_row_id(flyer_id) else None
    if flyer is None or flyer.deleted_at is not None:
        raise FlyerError(404, "Flyer not found")
    return flyer


def _like(column, q: str):
    return func.lower(column).contains(q.lower(), autoescape=True)


def _search_clause(q: Optional[str]):
    q = " ".join((q or "").split())
    if not q:
        return None
    return or_(_like(Flyer.title, q), _like(func.coalesce(Flyer.caption, ""), q))


def list_flyers(
    db: Session,
    category: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = DEFAULT_LIMIT,
    before_id: Optional[int] = None,
) -> dict:
    """{items, next_before_id, counts} - newest first, deleted ones left out.
    counts (additive, for the library's category chips) ignores the category
    filter and paging but honours `q`."""
    if category and category not in CATEGORY_LABELS:
        raise FlyerError(400, "Unknown category.")
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    search = _search_clause(q)
    stmt = select(Flyer).where(Flyer.deleted_at.is_(None))
    if search is not None:
        stmt = stmt.where(search)
    if category:
        stmt = stmt.where(Flyer.category == category)
    if before_id is not None and before_id <= MAX_ROW_ID:
        stmt = stmt.where(Flyer.id < before_id)
    rows = list(db.execute(stmt.order_by(Flyer.id.desc()).limit(limit + 1)).scalars())
    more = len(rows) > limit
    rows = rows[:limit]

    count_stmt = select(Flyer.category, func.count(Flyer.id)).where(Flyer.deleted_at.is_(None))
    if search is not None:
        count_stmt = count_stmt.where(search)
    per_category = dict(db.execute(count_stmt.group_by(Flyer.category)).all())
    counts = {"all": sum(per_category.values())}
    counts.update({key: per_category.get(key, 0) for key, _, _ in CATEGORIES})

    stats = _send_stats(db, [f.id for f in rows])
    return {
        "items": [flyer_out(f, stats.get(f.id)) for f in rows],
        "next_before_id": rows[-1].id if more and rows else None,
        "counts": counts,
    }


def _send_stats(db: Session, flyer_ids: List[int]) -> Dict[int, dict]:
    """flyer id -> {sent, failed, pending, last_sent_at}."""
    out: Dict[int, dict] = {}
    if not flyer_ids:
        return out
    rows = db.execute(
        select(FlyerSend.flyer_id, FlyerSend.status, func.count(FlyerSend.id), func.max(FlyerSend.sent_at))
        .where(FlyerSend.flyer_id.in_(flyer_ids))
        .group_by(FlyerSend.flyer_id, FlyerSend.status)
    ).all()
    for flyer_id, status, count, last_sent in rows:
        stats = out.setdefault(flyer_id, {"sent": 0, "failed": 0, "pending": 0, "last_sent_at": None})
        if status == SENT:
            stats["sent"] += count
            stats["last_sent_at"] = last_sent
        elif status == FAILED:
            stats["failed"] += count
        elif status in PENDING:
            stats["pending"] += count
    return out


def _iso(value: Optional[dt.datetime]) -> Optional[str]:
    return value.isoformat() if isinstance(value, dt.datetime) else None


def send_out(row: FlyerSend) -> dict:
    """A send as clients see it - never target_ref (the chat id)."""
    return {
        "id": row.id,
        "batch_id": row.batch_id,
        "channel": row.channel,
        "target_key": row.target_key,
        "target_label": row.target_label,
        "status": row.status,
        "error": row.error,
        "requested_by_name": row.requested_by_name,
        "requested_at": _iso(row.requested_at),
        "sent_at": _iso(row.sent_at),
    }


def flyer_out(flyer: Flyer, stats: Optional[dict] = None, sends: Optional[List[FlyerSend]] = None) -> dict:
    stats = stats or {"sent": 0, "failed": 0, "pending": 0, "last_sent_at": None}
    out = {
        "id": flyer.id,
        "title": flyer.title,
        "category": flyer.category,
        "category_label": CATEGORY_LABELS.get(flyer.category, flyer.category),
        "caption": flyer.caption,
        "file_name": display_file_name(flyer),
        "mime": flyer.mime,
        "kind": kind_for(flyer.mime),
        "size_bytes": flyer.size_bytes,
        "width": flyer.width,
        "height": flyer.height,
        "created_at": _iso(flyer.created_at),
        "created_by_name": flyer.created_by_name,
        "file_url": f"/api/flyers/{flyer.id}/file",
        "thumb_url": f"/api/flyers/{flyer.id}/thumb",
        "last_sent_at": _iso(stats.get("last_sent_at")),
        "send_counts": {"sent": stats["sent"], "failed": stats["failed"], "pending": stats["pending"]},
    }
    if sends is not None:
        out["sends"] = [send_out(s) for s in sends]
    return out


def flyer_detail(db: Session, flyer: Flyer) -> dict:
    sends = list(
        db.execute(select(FlyerSend).where(FlyerSend.flyer_id == flyer.id).order_by(FlyerSend.id.desc())).scalars()
    )
    return flyer_out(flyer, _send_stats(db, [flyer.id]).get(flyer.id), sends)


def update_flyer(db: Session, user: User, flyer_id: int, payload: dict) -> Flyer:
    """PATCH {title?, category?, caption?}. A new title or category renames /
    moves the file to match (same store date and id)."""
    flyer = get_flyer(db, flyer_id)
    title = clean_title(payload["title"]) if "title" in payload else flyer.title
    category = clean_category(payload["category"]) if "category" in payload else flyer.category
    caption = clean_caption(payload["caption"]) if "caption" in payload else flyer.caption
    with _FILE_LOCK:
        old_path = flyer_file(flyer)
        new_path = None
        if old_path is not None:
            # Found by its id after a rename / move in Finder: remember where.
            flyer.file_rel_path = _rel(old_path)
        if (title, category) != (flyer.title, flyer.category):
            if old_path is None:
                logger.warning("Flyer %s: file %r is missing; renaming the record only", flyer.id, flyer.file_rel_path)
            else:
                folder = ensure_dirs() / CATEGORY_FOLDERS[category]
                name = stored_file_name(flyer.id, title, flyer.created_at, flyer.mime)
                if folder / name != old_path:
                    new_path = _move(old_path, folder, name)
                    flyer.file_rel_path = _rel(new_path)
        flyer.title, flyer.category, flyer.caption = title, category, caption
        flyer.updated_at = utcnow()
        try:
            db.commit()
        except Exception:
            db.rollback()
            if new_path is not None and old_path is not None:
                _move(new_path, old_path.parent, old_path.name)
            raise
    db.refresh(flyer)
    return flyer


def delete_flyer(db: Session, user: User, flyer_id: int) -> None:
    """Soft delete: deleted_at is set and the file moves to
    _Deleted/<Category>/ - it is never removed outright. Sends still queued
    for it fail ("deleted before it was sent") when the worker reaches them."""
    flyer = get_flyer(db, flyer_id)
    with _FILE_LOCK:
        old_path = flyer_file(flyer)
        new_path = None
        if old_path is not None:
            new_path = _move(old_path, base_dir() / DELETED_DIR / CATEGORY_FOLDERS[flyer.category], old_path.name)
            flyer.file_rel_path = _rel(new_path)
        else:
            logger.warning("Flyer %s: file %r is missing; deleting the record only", flyer.id, flyer.file_rel_path)
        flyer.deleted_at = utcnow()
        flyer.updated_at = flyer.deleted_at
        try:
            db.commit()
        except Exception:
            db.rollback()
            if new_path is not None and old_path is not None:
                _move(new_path, old_path.parent, old_path.name)
            raise
    logger.info("Flyer %s deleted by %s (file kept at %s)", flyer.id, user.username, flyer.file_rel_path)


# --- Thumbnails --------------------------------------------------------------------------


def _thumbs_dir() -> pathlib.Path:
    path = base_dir() / THUMBS_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_thumb(img: Image.Image, dest: pathlib.Path) -> None:
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.split()[-1])
        img = flat
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((THUMB_SIDE, THUMB_SIDE), Image.LANCZOS)
    img.info = {}
    tmp = dest.with_name(f".{dest.name}.{secrets.token_hex(4)}.tmp")
    try:
        img.save(tmp, format="JPEG", quality=THUMB_QUALITY, optimize=True)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def _render_pdf_page(pdf: pathlib.Path, out_dir: pathlib.Path) -> Optional[pathlib.Path]:
    """First page of a PDF as a PNG, via macOS `sips`, then `qlmanage`.
    Both run with a timeout; None when neither works (not macOS, a PDF they
    can't read...)."""
    sips = shutil.which("sips") or "/usr/bin/sips"
    if os.path.exists(sips):
        out = out_dir / "page.png"
        try:
            subprocess.run(
                [sips, "-s", "format", "png", "-Z", str(THUMB_SIDE * 2), str(pdf), "--out", str(out)],
                capture_output=True, timeout=RENDER_TIMEOUT_SECONDS, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        if out.is_file() and out.stat().st_size:
            return out
    qlmanage = shutil.which("qlmanage") or "/usr/bin/qlmanage"
    if os.path.exists(qlmanage):
        try:
            subprocess.run(
                [qlmanage, "-t", "-s", str(THUMB_SIDE * 2), "-o", str(out_dir), str(pdf)],
                capture_output=True, timeout=RENDER_TIMEOUT_SECONDS, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        out = out_dir / f"{pdf.name}.png"
        if out.is_file() and out.stat().st_size:
            return out
    return None


def _pdf_icon() -> pathlib.Path:
    """A generic "PDF" page icon (PNG), drawn once and cached."""
    dest = _thumbs_dir() / "_pdf_icon.png"
    if dest.is_file():
        return dest
    img = Image.new("RGB", (360, 480), (245, 245, 245))
    draw = ImageDraw.Draw(img)
    draw.polygon([(60, 40), (240, 40), (300, 100), (300, 440), (60, 440)], fill=(255, 255, 255), outline=(150, 150, 150))
    draw.polygon([(240, 40), (240, 100), (300, 100)], fill=(225, 225, 225), outline=(150, 150, 150))
    draw.rectangle([(90, 230), (270, 300)], fill=(200, 40, 40))
    try:
        font = ImageFont.load_default(size=44)
    except TypeError:  # pragma: no cover - Pillow < 10.1
        font = ImageFont.load_default()
    draw.text((180, 265), "PDF", fill=(255, 255, 255), font=font, anchor="mm")
    tmp = dest.with_name(f".{dest.name}.{secrets.token_hex(4)}.tmp")
    try:
        img.save(tmp, format="PNG")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest


def thumbnail(flyer: Flyer) -> Tuple[pathlib.Path, str]:
    """(path, media type) of the flyer's thumbnail (at most THUMB_SIDE px),
    made on first request and cached in .thumbs/. Raises FlyerError(404)
    when the flyer's file is missing."""
    src = flyer_file(flyer)
    if src is None:
        raise FlyerError(404, "The flyer's file is missing from the Flyers folder.")
    thumbs = _thumbs_dir()
    dest = thumbs / f"{flyer.id}-{flyer.sha256[:12]}.jpg"
    if dest.is_file():
        return dest, "image/jpeg"
    if kind_for(flyer.mime) == "image":
        try:
            with Image.open(src) as img:
                img.draft("RGB", (THUMB_SIDE, THUMB_SIDE))
                img.load()
                _save_thumb(img, dest)
            return dest, "image/jpeg"
        except Exception:  # noqa: BLE001
            logger.exception("Flyer %s: couldn't make a thumbnail", flyer.id)
            raise FlyerError(404, "No preview for this flyer.")
    failed_marker = thumbs / f"{flyer.id}-{flyer.sha256[:12]}.none"
    if failed_marker.is_file() and time.time() - failed_marker.stat().st_mtime < THUMB_RETRY_AFTER_SECONDS:
        return _pdf_icon(), "image/png"
    with tempfile.TemporaryDirectory(prefix="flyer-thumb-") as tmp:
        page = _render_pdf_page(src, pathlib.Path(tmp))
        if page is not None:
            try:
                with Image.open(page) as img:
                    img.load()
                    _save_thumb(img, dest)
                failed_marker.unlink(missing_ok=True)
                return dest, "image/jpeg"
            except Exception:  # noqa: BLE001
                logger.exception("Flyer %s: couldn't read the rendered PDF page", flyer.id)
    failed_marker.touch()
    return _pdf_icon(), "image/png"


# --- Meta + sending ------------------------------------------------------------------------


def whatsapp_connected() -> bool:
    return bool(get_settings().whapi_api_token)


def meta(db: Session) -> dict:
    return {
        "categories": [{"key": key, "label": label} for key, label, _ in CATEGORIES],
        # Active groups an admin marked Marketing (and that have a chat to
        # send to), in label order - never their chat ids.
        "groups": [{"key": g["key"], "label": g["label"]} for g in whatsapp_groups.marketing_targets(db)],
        "whatsapp_connected": whatsapp_connected(),
        "max_image_mb": MAX_IMAGE_MB,
        "max_pdf_mb": MAX_PDF_MB,
        "accept": list(ACCEPT),
        "max_groups_per_send": MAX_GROUPS_PER_SEND,
        # Additive: the field limits the forms show.
        "max_title_chars": MAX_TITLE,
        "max_caption_chars": MAX_CAPTION,
    }


def _batch_out(rows: List[FlyerSend]) -> dict:
    return {"batch_id": rows[0].batch_id, "sends": [send_out(r) for r in rows]}


def _existing_batch(db: Session, flyer_id: int, user_id: int, key: str) -> List[FlyerSend]:
    """The batch this person already started with this idempotency key for
    this flyer within IDEMPOTENCY_WINDOW, or []."""
    since = utcnow() - IDEMPOTENCY_WINDOW
    first = db.execute(
        select(FlyerSend)
        .where(
            FlyerSend.flyer_id == flyer_id,
            FlyerSend.requested_by_user_id == user_id,
            FlyerSend.idempotency_key == key,
            FlyerSend.requested_at >= since,
        )
        .order_by(FlyerSend.id)
        .limit(1)
    ).scalars().first()
    if first is None:
        return []
    return list(
        db.execute(select(FlyerSend).where(FlyerSend.batch_id == first.batch_id).order_by(FlyerSend.id)).scalars()
    )


def _group_names(keys: List[str], db: Session) -> str:
    from app.models import WhatsAppGroup

    labels = dict(db.execute(select(WhatsAppGroup.key, WhatsAppGroup.label).where(WhatsAppGroup.key.in_(keys))).all())
    return ", ".join(labels.get(k, k) for k in keys)


def create_send_batch(db: Session, user: User, flyer_id: int, payload) -> Tuple[dict, List[int]]:
    """POST /{id}/send {group_keys, idempotency_key} -> ({batch_id, sends},
    ids of the NEW rows for the worker - [] when the key was seen before)."""
    flyer = get_flyer(db, flyer_id)
    if not isinstance(payload, dict):
        raise FlyerError(400, "Send a JSON object.")
    key = payload.get("idempotency_key")
    if not isinstance(key, str) or not key.strip() or len(key.strip()) > MAX_IDEMPOTENCY_KEY:
        raise FlyerError(400, "idempotency_key is required (a unique id for this send, e.g. a UUID).")
    key = key.strip()
    group_keys = payload.get("group_keys")
    if not isinstance(group_keys, list) or not all(isinstance(k, str) for k in group_keys):
        raise FlyerError(400, "Choose the WhatsApp groups to send to.")
    keys = list(dict.fromkeys(k.strip() for k in group_keys if k.strip()))
    with _SEND_LOCK:
        existing = _existing_batch(db, flyer.id, user.id, key)
        if existing:
            return _batch_out(existing), []
        if not keys:
            raise FlyerError(400, "Choose at least one WhatsApp group.")
        if len(keys) > MAX_GROUPS_PER_SEND:
            raise FlyerError(400, f"Choose at most {MAX_GROUPS_PER_SEND} groups at a time.")
        targets = {g["key"]: g for g in whatsapp_groups.marketing_targets(db)}
        bad = [k for k in keys if k not in targets]
        if bad:
            raise FlyerError(
                400,
                f"Flyers can't go to: {_group_names(bad, db)}. Only active groups marked \"Use for flyers\" can get "
                "flyers.",
            )
        if not whatsapp_connected():
            raise FlyerError(409, NOT_CONNECTED)
        now = utcnow()
        batch_id = str(uuid.uuid4())
        rows = [
            FlyerSend(
                flyer_id=flyer.id,
                batch_id=batch_id,
                channel=CHANNEL_WHATSAPP,
                target_key=k,
                target_label=targets[k]["label"],
                target_ref=targets[k]["chat_id"],
                status=QUEUED,
                requested_by_user_id=user.id,
                requested_by_name=user.username,
                requested_at=now,
                queued_at=now,
                idempotency_key=key,
            )
            for k in keys
        ]
        db.add_all(rows)
        db.commit()
    for row in rows:
        db.refresh(row)
    logger.info("Flyer %s: batch %s queued for %d group(s) by %s", flyer.id, batch_id, len(rows), user.username)
    return _batch_out(rows), [r.id for r in rows]


def retry_send(db: Session, user: User, flyer_id: int, send_id: int) -> Tuple[dict, List[int]]:
    """Puts one failed send back in the queue (same group, re-checked: it must
    still be an active marketing group)."""
    flyer = get_flyer(db, flyer_id)
    row = db.get(FlyerSend, send_id) if _valid_row_id(send_id) else None
    if row is None or row.flyer_id != flyer.id:
        raise FlyerError(404, "Send not found")
    with _SEND_LOCK:
        db.refresh(row)
        if row.status != FAILED:
            raise FlyerError(409, "Only a failed send can be retried.")
        target = {g["key"]: g for g in whatsapp_groups.marketing_targets(db)}.get(row.target_key)
        if target is None:
            raise FlyerError(
                400, f"{row.target_label} isn't an active group marked \"Use for flyers\" any more, so it can't get flyers."
            )
        if row.channel != CHANNEL_WHATSAPP:
            raise FlyerError(400, "That channel isn't supported.")
        if not whatsapp_connected():
            raise FlyerError(409, NOT_CONNECTED)
        row.target_ref = target["chat_id"]
        row.status = QUEUED
        row.error = None
        row.queued_at = utcnow()
        db.commit()
    db.refresh(row)
    logger.info("Flyer %s: send %s (%s) queued again by %s", flyer.id, row.id, row.target_key, user.username)
    return send_out(row), [row.id]
