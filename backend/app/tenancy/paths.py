"""Per-store files (docs/PLATFORM_CONTRACT.md 7).

Multi mode: a store's files live under ``<UPLOADS_ROOT>/<STORE_CODE>/``
(``team/``, ``invoices/``, ``flyers/``, ``.keys/``, the identity marker);
the settings overlay points the old settings (``team_uploads_dir``...)
there, so most call sites never change. Single mode: ``UPLOADS_ROOT`` itself
(as in M2). ``store_path`` refuses anything that escapes the store's root.
"""

import pathlib
from typing import Optional

from app.tenancy.errors import NoStoreContext
from app.tenancy.mode import is_multi_tenant
from app.tenancy.registry import is_valid_store_code, normalize_store_code


def uploads_base() -> pathlib.Path:
    """UPLOADS_ROOT of the process (the parent of every store's root in multi mode)."""
    from app.config import process_settings

    raw = (process_settings().uploads_root or "uploads").strip() or "uploads"
    return pathlib.Path(raw).expanduser().resolve()


def store_root(code: Optional[str] = None) -> pathlib.Path:
    """<UPLOADS_ROOT>/<CODE> for `code` or the current store (multi mode);
    UPLOADS_ROOT in single mode (the current settings' value)."""
    if not is_multi_tenant():
        from app.config import get_settings

        raw = (getattr(get_settings(), "uploads_root", "") or "uploads").strip() or "uploads"
        return pathlib.Path(raw).expanduser().resolve()
    if code is None:
        from app.tenancy.context import current_store_code

        code = current_store_code()
        if code is None:
            raise NoStoreContext()
    norm = normalize_store_code(code)
    if not is_valid_store_code(norm):
        raise ValueError(f"not a store code: {code!r}")
    return uploads_base() / norm


def store_path(*parts: str, code: Optional[str] = None, create: bool = False) -> pathlib.Path:
    """A path under the store's root. Refuses absolute parts, ``..`` and
    anything that resolves (symlinks included) outside the root.
    ``create=True`` makes the path as a directory."""
    root = store_root(code)
    for part in parts:
        p = pathlib.PurePath(str(part))
        if p.is_absolute() or str(part).startswith(("/", "\\")) or ".." in p.parts or "\\" in str(part):
            raise ValueError(f"path part {part!r} is not allowed")
    path = root.joinpath(*[str(p) for p in parts])
    resolved = path.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
        raise ValueError("path escapes the store's folder")
    if create:
        resolved.mkdir(parents=True, exist_ok=True)
    return resolved
