"""Images for the Team hub (task proof, chat, announcements) - validated,
re-encoded and stored by the server no matter what the client already did
(design §9.1). Nothing about an upload is trusted: not its declared type,
not its name, not its size, not its contents.

1. Declared type must be JPEG, PNG, WebP or HEIC/HEIF (else 415).
2. At most MAX_UPLOAD_BYTES: the request's Content-Length is checked before
   the multipart body is parsed (so a huge body is never spooled to disk),
   and the file is counted again while reading (else 413).
3. Pillow must recognize it, `verify()` it, and fully decode it, as one of
   those same formats (else 415).
4. At most MAX_PIXELS pixels, checked from the header before decoding, so a
   small file that expands to a huge bitmap is refused (else 415).
5. Re-encoded as a fresh JPEG: shrunk to at most MAX_SIDE px *before* any
   colour conversion (so a big image never exists as several full-size
   copies), EXIF orientation applied, then all metadata (EXIF/GPS, comments,
   ICC) dropped. At most _PROCESSING_SLOTS images are decoded at once.
6. Stored as uploads/team/YYYY/MM/<random>.jpg - the original filename and
   path are never used - via a temp file + fsync + rename, and removed again
   if the database row can't be saved.
7. Served only by GET /api/team/media/{token} after an access check.
"""

import io
import logging
import pathlib
import os
import secrets
import threading
from typing import Callable, Dict, Optional, Tuple

from fastapi import HTTPException, Request, UploadFile
from PIL import Image, ImageOps
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User
from app.team.models import TeamMedia
from app.timeutil import utcnow

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:  # pragma: no cover - pillow-heif is in requirements.txt
    HEIC_SUPPORTED = False

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# Whole-request cap checked from Content-Length before parsing: the file
# plus multipart headers/boundaries (and any small form fields).
MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + 1024 * 1024
MAX_PIXELS = 50_000_000  # e.g. 8000x6250 - well above any phone camera
MAX_SIDE = 2048
JPEG_QUALITY = 85

ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/pjpeg",
    "image/png",
    "image/webp",
    "image/heic",
    "image/heif",
    "image/heic-sequence",
    "image/heif-sequence",
}
# MPO = a JPEG carrying extra frames, which is what iPhone HDR (gain map) and
# Android Ultra HDR photos are; only the first frame is used.
ALLOWED_FORMATS = {"JPEG", "MPO", "PNG", "WEBP", "HEIF"}

# A decoded image can be many times its file size (50 MP RGBA ~ 200 MB), so
# at most this many are processed at once, whatever the request concurrency.
_PROCESSING_SLOTS = threading.BoundedSemaphore(2)

_CHUNK = 256 * 1024

# owner_type of a photo uploaded through a public checklist link
# (services/checklist_public.py) and not yet used in a submission; owner_id
# is the link id. Nobody can read these (no access hook); the nightly prune
# removes them like unattached uploads.
PUBLIC_PENDING_OWNER_TYPE = "checklist_public_upload"


class MediaRejected(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# --- Access hooks -------------------------------------------------------------

# owner_type -> (db, user, owner_id) -> may this user see media attached to it?
_access_checks: Dict[str, Callable[[Session, User, int], bool]] = {}


def register_media_access(owner_type: str, fn: Callable[[Session, User, int], bool]) -> None:
    """Called by the phase that attaches media to something (e.g. "task",
    "message", "announcement"): decides who else may see it."""
    _access_checks[owner_type] = fn


def can_access(db: Session, user: User, media: TeamMedia) -> bool:
    if user.is_admin or media.uploaded_by_user_id == user.id:
        return True
    if media.owner_type is None or media.owner_id is None:
        return False
    check = _access_checks.get(media.owner_type)
    if check is None:
        return False
    try:
        return bool(check(db, user, media.owner_id))
    except Exception:  # noqa: BLE001 - deny on any error
        logger.exception("Media access check for %s failed", media.owner_type)
        return False


# --- Validation and storage ------------------------------------------------------


def _uploads_root() -> pathlib.Path:
    return pathlib.Path(get_settings().team_uploads_dir)


# Object storage (website-ready C2, app.storage I1). With the default local
# backend nothing changes: files stay under team_uploads_dir exactly as
# before. With STORAGE_BACKEND=s3 a file is the store's object
# "team/<media.path>" (the same name the migration tool gives the local file
# <store folder>/team/<media.path>).
STORAGE_PREFIX = "team"


def _object_storage():
    """The S3 / R2 storage when STORAGE_BACKEND=s3, else None (local files)."""
    from app.storage import backend_name, get_storage

    return get_storage() if backend_name() == "s3" else None


def media_key(rel: str) -> str:
    return f"{STORAGE_PREFIX}/{rel}"


def check_request_size(request: Request) -> None:
    """Refuses an upload request by its Content-Length alone, before the
    multipart body is read - FastAPI/Starlette would otherwise spool the
    whole body to a temp file first, however large. Use it in any route that
    accepts an image (call it before touching the form). A body without a
    Content-Length (chunked) is refused too: every client we have sends one."""
    raw = request.headers.get("content-length")
    if raw is None:
        raise HTTPException(status_code=411, detail="Upload needs a Content-Length.")
    try:
        length = int(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Bad Content-Length.")
    if length > MAX_REQUEST_BYTES:
        raise HTTPException(status_code=413, detail="That image is too large (10 MB max).")


def _read_limited(upload: UploadFile) -> bytes:
    buf = bytearray()
    while True:
        chunk = upload.file.read(_CHUNK)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > MAX_UPLOAD_BYTES:
            raise MediaRejected(413, "That image is too large (10 MB max).")
    return bytes(buf)


def process_image(data: bytes, content_type: Optional[str]) -> Tuple[bytes, int, int]:
    """Validates and re-encodes raw upload bytes. Returns (jpeg bytes, width,
    height). Raises MediaRejected."""
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared not in ALLOWED_CONTENT_TYPES:
        raise MediaRejected(415, "Only JPEG, PNG, WebP or HEIC photos can be uploaded.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise MediaRejected(413, "That image is too large (10 MB max).")
    if not data:
        raise MediaRejected(415, "The file is empty.")

    with _PROCESSING_SLOTS:
        try:
            with Image.open(io.BytesIO(data)) as probe:
                fmt = probe.format
                width, height = probe.size
                if fmt not in ALLOWED_FORMATS:
                    raise MediaRejected(415, "That file isn't a supported image.")
                # The header check that guards against decompression bombs,
                # before anything is decoded.
                if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
                    raise MediaRejected(415, "That image's dimensions are too large.")
                probe.verify()
            # verify() leaves the image unusable - reopen to actually decode.
            with Image.open(io.BytesIO(data)) as img:
                if img.format in ("JPEG", "MPO"):
                    # Let libjpeg decode at 1/2, 1/4 or 1/8 scale straight
                    # away when the photo is much bigger than we keep.
                    img.draft("RGB", (MAX_SIDE, MAX_SIDE))
                img.load()
                # Shrink first, in the image's own mode, so the colour
                # conversion / alpha flattening below only ever touches the
                # small image (before, a big PNG existed as ~4 full-size
                # copies). Pillow still premultiplies RGBA/LA on one
                # full-size copy inside reduce/thumbnail - unavoidable, so
                # those skip the extra reduce step.
                factor = max(img.size) // MAX_SIDE
                if factor >= 2 and img.mode not in ("P", "1", "RGBA", "LA"):
                    try:
                        img = img.reduce(factor)
                    except ValueError:  # a mode reduce() doesn't support
                        pass
                img.thumbnail((MAX_SIDE, MAX_SIDE))
                img = ImageOps.exif_transpose(img)
                if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                    rgba = img.convert("RGBA")
                    flat = Image.new("RGB", rgba.size, (255, 255, 255))
                    flat.paste(rgba, mask=rgba.split()[-1])
                    img = flat
                elif img.mode != "RGB":
                    img = img.convert("RGB")
                # Nothing from the original rides along: Pillow writes a
                # JPEG comment (and ICC profile) from .info if present.
                img.info = {}
                out = io.BytesIO()
                img.save(out, format="JPEG", quality=JPEG_QUALITY, optimize=True)
                return out.getvalue(), img.width, img.height
        except MediaRejected:
            raise
        except Image.DecompressionBombError:
            raise MediaRejected(415, "That image's dimensions are too large.")
        except Exception:  # noqa: BLE001 - anything Pillow can't read is not an image
            raise MediaRejected(415, "That file isn't a valid image.")


def _write_atomically(dest: pathlib.Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{secrets.token_hex(4)}.tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            tmp.unlink()


# --- Voice notes and documents (W5, migration 0055) ----------------------------
# Chat only. Neither can be re-encoded here (no audio/PDF toolchain on the
# server), so each is checked by its real contents instead of its declared
# type, stored under a random server-made name, and served with its fixed
# content type plus nosniff.

KIND_IMAGE = "image"
KIND_AUDIO = "audio"
KIND_DOCUMENT = "document"
KINDS = (KIND_IMAGE, KIND_AUDIO, KIND_DOCUMENT)

AUDIO_CONTENT_TYPES = {"audio/mp4", "audio/m4a", "audio/x-m4a", "audio/aac", "audio/mp4a-latm", "video/mp4"}
DOCUMENT_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}
# A voice note: AAC in an MP4 (.m4a) - what iPhones, Android (the `record`
# plugin's AAC-LC) and Safari/Chrome's MediaRecorder("audio/mp4") all make,
# and what every one of them can play back.
MAX_VOICE_SECONDS = 300
MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_PAGES = 300
MAX_DISPLAY_NAME = 120


def kind_for_content_type(content_type: Optional[str]) -> Optional[str]:
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared in ALLOWED_CONTENT_TYPES:
        return KIND_IMAGE
    if declared in AUDIO_CONTENT_TYPES:
        return KIND_AUDIO
    if declared in DOCUMENT_CONTENT_TYPES:
        return KIND_DOCUMENT
    return None


def _mp4_boxes(data: bytes, start: int, end: int):
    """(type, payload start, box end) for each box in data[start:end]."""
    pos = start
    while pos + 8 <= end:
        size = int.from_bytes(data[pos:pos + 4], "big")
        kind = data[pos + 4:pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > end:
                return
            size = int.from_bytes(data[pos + 8:pos + 16], "big")
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield kind, pos + header, pos + size
        pos += size


def _find_box(data: bytes, path, start: int, end: int):
    """The (payload start, end) of the box at `path` (e.g. moov/mvhd)."""
    for kind, a, b in _mp4_boxes(data, start, end):
        if kind == path[0]:
            if len(path) == 1:
                return a, b
            return _find_box(data, path[1:], a, b)
    return None


def process_audio(data: bytes, duration_hint_ms: Optional[int] = None) -> int:
    """Checks a voice note and returns its duration in ms. Raises
    MediaRejected. Must be an MP4 container (ftyp first, then moov) whose
    audio track is AAC ("mp4a"); the duration comes from the file's own
    header when it has one (Chrome writes a fragmented MP4 without it - then
    the recorder's own figure, capped)."""
    if not data:
        raise MediaRejected(415, "The recording is empty.")
    if len(data) > MAX_AUDIO_BYTES:
        raise MediaRejected(413, "That recording is too large (10 MB max).")
    if data[4:8] != b"ftyp":
        raise MediaRejected(415, "Voice notes must be recorded in the app (AAC audio).")
    moov = _find_box(data, (b"moov",), 0, len(data))
    if moov is None:
        raise MediaRejected(415, "That recording isn't complete - record it again.")
    if b"mp4a" not in data[moov[0]:moov[1]]:
        raise MediaRejected(415, "Voice notes must be AAC audio.")
    duration_ms = 0
    mvhd = _find_box(data, (b"moov", b"mvhd"), 0, len(data))
    if mvhd is not None:
        a = mvhd[0]
        version = data[a]
        try:
            if version == 1:
                timescale = int.from_bytes(data[a + 20:a + 24], "big")
                duration = int.from_bytes(data[a + 24:a + 32], "big")
            else:
                timescale = int.from_bytes(data[a + 12:a + 16], "big")
                duration = int.from_bytes(data[a + 16:a + 20], "big")
        except IndexError:  # pragma: no cover - slicing never raises
            timescale = duration = 0
        if timescale > 0 and duration not in (0, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF):
            duration_ms = duration * 1000 // timescale
    if duration_ms <= 0 and duration_hint_ms:
        duration_ms = max(0, int(duration_hint_ms))
    if duration_ms > (MAX_VOICE_SECONDS + 2) * 1000:
        raise MediaRejected(413, f"Voice notes can be up to {MAX_VOICE_SECONDS // 60} minutes.")
    return min(duration_ms, MAX_VOICE_SECONDS * 1000)


def process_document(data: bytes) -> int:
    """Checks a PDF (the flyers check: opens with pypdf under an inflate cap,
    not password-protected) and returns its page count. Raises
    MediaRejected."""
    if not data:
        raise MediaRejected(415, "The file is empty.")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise MediaRejected(413, "That PDF is too large (10 MB max).")
    if not data[:1024].lstrip().startswith(b"%PDF-"):
        raise MediaRejected(415, "Only PDF documents can be sent.")
    from app.services import flyers

    try:
        flyers._check_pdf(data)
    except flyers.FlyerError as e:
        raise MediaRejected(415, e.detail)
    try:
        from pypdf import PdfReader

        pages = len(PdfReader(io.BytesIO(data), strict=False).pages)
    except Exception:  # noqa: BLE001 - _check_pdf already opened it once
        pages = 0
    if pages > MAX_DOCUMENT_PAGES:
        raise MediaRejected(413, f"That PDF has too many pages ({MAX_DOCUMENT_PAGES} max).")
    return pages


def clean_display_name(name: Optional[str], kind: str) -> Optional[str]:
    """A document's name for display: the last path part, no control or
    bidi characters, word-filtered, at most MAX_DISPLAY_NAME, always .pdf.
    Never used for the stored path."""
    if kind != KIND_DOCUMENT:
        return None
    raw = (name or "").replace("\\", "/").split("/")[-1]
    raw = "".join(
        ch for ch in raw
        if ch.isprintable() and ch not in "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
    ).strip()
    from app.team import content_filter

    raw = content_filter.clean(raw) or ""
    stem = raw[:-4] if raw.lower().endswith(".pdf") else raw
    stem = stem.strip(" .")[: MAX_DISPLAY_NAME - 4] or "Document"
    return f"{stem}.pdf"


def save_upload(
    db: Session,
    user: Optional[User],
    upload: UploadFile,
    owner_type: Optional[str] = None,
    owner_id: Optional[int] = None,
    *,
    allowed_kinds=(KIND_IMAGE,),
    duration_ms: Optional[int] = None,
) -> TeamMedia:
    """Validates, re-encodes and stores an upload; commits the TeamMedia row.
    Raises HTTPException (413/415) for anything rejected. `user` None = a
    logged-out public checklist link (then owner_type/owner_id bind it to
    that link). `allowed_kinds` adds voice notes / PDFs (chat only)."""
    kind = kind_for_content_type(upload.content_type)
    if kind not in allowed_kinds:
        kind = KIND_IMAGE  # the image check below gives the usual 415
    width = height = 0
    pages = None
    display = None
    try:
        if kind == KIND_IMAGE:
            data = _read_limited(upload)
            stored, width, height = process_image(data, upload.content_type)
            content_type, ext = "image/jpeg", "jpg"
        elif kind == KIND_AUDIO:
            stored = _read_limited(upload)
            duration_ms = process_audio(stored, duration_ms)
            content_type, ext = "audio/mp4", "m4a"
        else:
            stored = _read_limited(upload)
            pages = process_document(stored)
            content_type, ext = "application/pdf", "pdf"
            display = clean_display_name(upload.filename, kind)
    except MediaRejected as e:
        raise HTTPException(status_code=e.status_code, detail=e.detail)

    now = utcnow()
    rel = pathlib.Path(f"{now:%Y}") / f"{now:%m}" / f"{secrets.token_urlsafe(24)}.{ext}"
    remote = _object_storage()
    dest = _uploads_root() / rel
    if remote is not None:
        remote.put_bytes(media_key(rel.as_posix()), stored, content_type=content_type)
    else:
        _write_atomically(dest, stored)

    media = TeamMedia(
        token=secrets.token_urlsafe(24),
        path=rel.as_posix(),
        content_type=content_type,
        bytes=len(stored),
        width=width,
        height=height,
        uploaded_by_user_id=user.id if user is not None else None,
        owner_type=owner_type,
        owner_id=owner_id,
        created_at=now,
        kind=kind,
        display_name=display,
        duration_ms=duration_ms if kind == KIND_AUDIO else None,
        page_count=pages,
    )
    db.add(media)
    try:
        db.commit()
    except Exception:
        db.rollback()
        if remote is not None:
            remote.delete(media_key(rel.as_posix()))
        else:
            dest.unlink(missing_ok=True)
        raise
    db.refresh(media)
    return media


def media_out(media: TeamMedia) -> dict:
    out = {
        "id": media.id,
        "token": media.token,
        "url": f"/api/team/media/{media.token}",
        "width": media.width,
        "height": media.height,
    }
    kind = media.kind or KIND_IMAGE
    if kind != KIND_IMAGE:
        # Additive (W5), voice notes and PDFs only - a photo keeps exactly the
        # shape every build (and the public checklist page) knows; clients
        # read a missing `kind` as "image".
        out.update(
            kind=kind,
            content_type=media.content_type,
            bytes=media.bytes,
            name=media.display_name,
            duration_ms=media.duration_ms,
            page_count=media.page_count,
        )
    return out


def attach_media(
    db: Session,
    user: User,
    token: str,
    owner_type: str,
    owner_id: int,
    kinds=(KIND_IMAGE,),
) -> TeamMedia:
    """Links an upload to the thing it belongs to (caller commits). Only the
    uploader can attach their own, still-unattached upload - so one user
    can't graft someone else's photo onto their task. `kinds`: what may be
    attached there (only chat messages take voice notes and PDFs)."""
    media = db.execute(select(TeamMedia).where(TeamMedia.token == token)).scalars().first()
    if media is None or media.uploaded_by_user_id != user.id:
        raise HTTPException(status_code=400, detail="Unknown photo - upload it again.")
    if (media.kind or KIND_IMAGE) not in kinds:
        raise HTTPException(status_code=400, detail="Only a photo can be attached here.")
    if media.owner_type is not None and (media.owner_type, media.owner_id) != (owner_type, owner_id):
        raise HTTPException(status_code=400, detail="That photo is already used elsewhere.")
    media.owner_type = owner_type
    media.owner_id = owner_id
    db.add(media)
    return media


def media_file(media: TeamMedia) -> pathlib.Path:
    root = _uploads_root().resolve()
    path = (root / media.path).resolve()
    # Paths are server-generated, but never serve outside the uploads root.
    if root not in path.parents:
        raise HTTPException(status_code=404, detail="Not found")
    return path


def media_response(media: TeamMedia, headers: dict):
    """The file as an HTTP response, or None when it is missing: a
    FileResponse from the local folder, or (object storage) a stream from
    the bucket through this server - same URL, same headers, so the
    website's CSP and the apps never see the bucket."""
    remote = _object_storage()
    if remote is None:
        path = media_file(media)
        if not path.is_file():
            return None
        from fastapi.responses import FileResponse

        return FileResponse(path, media_type=media.content_type, headers=headers)
    from app.storage import ObjectNotFound, StorageError

    key = media_key(media.path)
    try:
        fh = remote.open(key)
    except ObjectNotFound:
        return None
    except StorageError:
        logger.exception("Team media %s could not be read from object storage", media.path)
        raise HTTPException(status_code=503, detail="This file can't be loaded right now. Try again soon.")
    from fastapi.responses import StreamingResponse

    def chunks():
        try:
            while True:
                block = fh.read(64 * 1024)
                if not block:
                    break
                yield block
        finally:
            fh.close()

    out = dict(headers)
    if media.bytes:
        out["Content-Length"] = str(media.bytes)
    return StreamingResponse(chunks(), media_type=media.content_type, headers=out)


def delete_media_file(media: TeamMedia) -> None:
    try:
        remote = _object_storage()
        if remote is not None:
            remote.delete(media_key(media.path))
        else:
            media_file(media).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        logger.exception("Could not delete team media file %s", media.path)


def media_for_token(db: Session, token: str) -> Optional[TeamMedia]:
    return db.execute(select(TeamMedia).where(TeamMedia.token == token)).scalars().first()


# Uploads that were never attached to anything (the app/website uploads
# first, then sends the token with the message/task/announcement) - e.g. a
# photo picked and then abandoned. Removed after this long.
UNATTACHED_MAX_AGE_HOURS = 24


def prune_unattached_media(db: Session) -> int:
    """Nightly: deletes unattached uploads (rows and files) older than
    UNATTACHED_MAX_AGE_HOURS. Returns how many were removed."""
    import datetime as dt

    cutoff = utcnow() - dt.timedelta(hours=UNATTACHED_MAX_AGE_HOURS)
    from sqlalchemy import or_

    stale = db.execute(
        select(TeamMedia).where(
            or_(TeamMedia.owner_type.is_(None), TeamMedia.owner_type == PUBLIC_PENDING_OWNER_TYPE),
            TeamMedia.created_at < cutoff,
        )
    ).scalars().all()
    for media in stale:
        delete_media_file(media)
        db.delete(media)
    db.commit()
    if stale:
        logger.info("Removed %d unattached team upload(s)", len(stale))
    return len(stale)
