"""Load a store's private pack from a file OUTSIDE any git checkout.

A private pack holds one store's own rules (menus, PO sections, vendors,
checklists...). It is never committed: the loader refuses a file that sits
inside a git work tree (any parent folder with ``.git``; symbolic links are
followed first) or is a hard link (its content may also sit in a checkout),
so a pack can't be loaded from a checkout by mistake - and so the habit of
keeping it in one never starts.

    python -m app.platform load-pack CODE --file ~/somewhere-private/store.pack.json [--no-rows]

What it does, on that store's own database (shared service):
1. validates the pack (app/store/pack.py - the same rules as store-init);
2. saves it as the store's active pack (the newest ``store_pack`` row of
   setting_changes; nothing is written when the same pack is already there);
3. unless --no-rows, adds the pack's checklists, WhatsApp roles and category
   rules that the store doesn't have yet (never changes or removes a row);
4. records ``store.pack_loaded`` in the platform audit log (pack id, file
   name and its SHA-256 - never the pack's content).
A running server picks the new pack up after a restart.
"""

import hashlib
import pathlib
from typing import Any, Dict, Optional, Union


class PrivatePackError(RuntimeError):
    pass


def git_work_tree(path: Union[str, pathlib.Path]) -> Optional[pathlib.Path]:
    """The git work tree `path` is inside (the folder holding ``.git``), or None."""
    p = pathlib.Path(path).expanduser().resolve()
    for folder in [p] + list(p.parents):
        if (folder / ".git").exists():
            return folder
    return None


def check_outside_git(path: Union[str, pathlib.Path]) -> pathlib.Path:
    p = pathlib.Path(path).expanduser().resolve()
    if not p.is_file():
        raise PrivatePackError(f"{p} is not a file")
    tree = git_work_tree(p)
    if tree is not None:
        raise PrivatePackError(
            f"{p} is inside the git checkout {tree}. Private packs live outside git "
            "(for example ~/.kloudc-retailops/private/): move the file and try again."
        )
    if p.stat().st_nlink > 1:
        # A hard link shares its content with another path - possibly one in
        # a checkout - and nothing tells where the other names are.
        raise PrivatePackError(
            f"{p} is a hard link ({p.stat().st_nlink} names for one file). Keep one private copy "
            "outside git (cp it to a new file) and load that."
        )
    return p


def sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_private_pack(code: str, path: Union[str, pathlib.Path], *, rows: bool = True,
                      actor: str = "cli") -> Dict[str, Any]:
    from sqlalchemy import select

    from app.platform import bridge
    from app.platform.audit import audit
    from app.platform.db import platform_session
    from app.platform.models import Store
    from app.platform.provisioning import _store_db
    from app.store import pack as store_pack
    from app.store.__main__ import insert_pack_rows, write_pack_row
    from app.store.identity import read_stamp

    p = check_outside_git(path)
    try:
        pack = store_pack.load_pack_file(p)
    except store_pack.PackError as exc:
        raise PrivatePackError(f"the pack is not valid: {exc}") from None
    code = (code or "").strip().upper()
    digest = sha256_file(p)
    with platform_session() as pdb:
        store = pdb.execute(select(Store).where(Store.code == code)).scalar_one_or_none()
        if store is None:
            raise PrivatePackError(f"no store {code}")
        if store.status not in ("active", "suspended"):
            raise PrivatePackError(f"store {code} is {store.status}: set it up first")
        if store.hosting != "shared" or store.db_host_ref:
            raise PrivatePackError(f"store {code} is on its own server: load the pack there")
        with _store_db(store) as sdb, bridge.store_context(code, allow_inactive=True):
            stamp = read_stamp(sdb)
            if stamp is None or stamp.get("store_code") != code:
                raise PrivatePackError(f"the database of {code} is not stamped for {code} (stamp: {stamp})")
            author = f"private pack ({pack.pack_id})"
            try:
                written = write_pack_row(sdb, pack, author)
                inserted = insert_pack_rows(sdb, pack, author) if rows else {}
                sdb.commit()
            except Exception:
                sdb.rollback()
                raise
        details = {"pack_id": pack.pack_id, "file": p.name, "sha256": digest, "written": written,
                   "inserted": inserted}
        audit("store.pack_loaded", actor_type="system", actor_name=actor, store_code=code, details=details, db=pdb)
        pdb.commit()
    return {"store_code": code, **details, "restart_needed": bool(written or any((inserted or {}).values()))}
