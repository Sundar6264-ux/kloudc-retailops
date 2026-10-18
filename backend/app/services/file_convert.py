"""Converts an uploaded invoice photo/scan to a PDF for the Toast email
attachment (Toast's inbox only turns a PDF into a draft bill automatically -
per the user's own description of the existing Google Form workflow, "email
the uploaded invoice/something else by converting into pdf (only if pdf is
not uploaded)"). A plain PDF upload is used as-is, no conversion needed.
"""

import pathlib

from PIL import Image

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIC_SUPPORTED = True
except ImportError:  # pragma: no cover - pillow-heif is a real requirements.txt entry
    HEIC_SUPPORTED = False

SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".heic", ".heif"}


class UnsupportedFileType(Exception):
    pass


def convert_to_pdf(src_path: pathlib.Path, dst_path: pathlib.Path) -> None:
    """Raises UnsupportedFileType for anything that isn't a recognized image
    format - the caller is expected to catch this, record it on the
    submission's toast_email_error, and skip sending the email rather than
    fail the whole submission (the file itself is still saved either way).
    """
    suffix = src_path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_SUFFIXES:
        raise UnsupportedFileType(
            f"Don't know how to convert '{suffix}' files to PDF - upload a PDF directly, or a "
            "photo (jpg/png/heic/etc.), for the Toast email to attach anything."
        )
    if suffix in {".heic", ".heif"} and not HEIC_SUPPORTED:
        raise UnsupportedFileType("HEIC support isn't installed on the server (pillow-heif missing).")
    image = Image.open(src_path)
    image.convert("RGB").save(dst_path, "PDF")
