#!/usr/bin/env python3
"""Encrypted off-site copy of the newest nightly backup (pre-production
2026-10-21, docs/GO_LIVE_RUNBOOK.md "Off-site backup").

    python offsite_copy.py copy --dest ~/kloudc-retailops/backups --to "<off-site folder>" \\
        --key-file ~/kloudc-retailops/secrets/offsite-backup.key [--uploads ~/kloudc-retailops/uploads] [--keep 14]
    python offsite_copy.py open --file <copy>.krbak --key-file <key> --out <empty folder>

``copy`` packs the newest dump of every folder under --dest (the platform
and each store, as backup_all.py left them) and, with --uploads, the store
files, into one tar, encrypts it and writes ``retailops-<stamp>.krbak`` into
--to (an iCloud Drive folder by default - no new account; any synced folder
or mounted disk works). The copy is written as ``.part`` and renamed when
complete; only the newest --keep copies are kept.

Encryption: AES-256-GCM in 4 MiB chunks, each with its own random nonce and
its index (and a "last chunk" flag) bound in, so a cut, reordered or edited
file never opens. The key is 32 random bytes in --key-file, created on the
first run (mode 600). **Keep a copy of that key somewhere else (a password
manager)**: without it the off-site copies can't be opened; with it they can,
so it never goes into the off-site folder.

``open`` decrypts and unpacks a copy into an empty folder (restore:
docs/PILOT_RUNBOOK.md, then pg_restore each dump).
"""

import argparse
import base64
import datetime as dt
import io
import json
import os
import pathlib
import secrets
import struct
import sys
import tarfile
import tempfile
from typing import List, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"KRBAK1\n"
CHUNK = 4 * 1024 * 1024
SUFFIX = ".krbak"
DEFAULT_KEEP = 14


class OffsiteError(RuntimeError):
    pass


def load_or_create_key(path: pathlib.Path, create: bool = True) -> bytes:
    if path.is_file():
        try:
            key = base64.b64decode(path.read_text().strip(), validate=True)
        except ValueError as exc:
            raise OffsiteError(f"{path} is not a valid key file") from exc
        if len(key) != 32:
            raise OffsiteError(f"{path} does not hold a 32-byte key")
        return key
    if not create:
        raise OffsiteError(f"key file {path} not found")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(base64.b64encode(key).decode() + "\n")
    print(f"offsite: created a new key {path} - keep a copy in your password manager", file=sys.stderr)
    return key


def _aad(index: int, last: bool) -> bytes:
    return MAGIC + struct.pack(">QB", index, 1 if last else 0)


def encrypt_stream(src, dst, key: bytes) -> None:
    aes = AESGCM(key)
    dst.write(MAGIC)
    index = 0
    chunk = src.read(CHUNK)
    while True:
        nxt = src.read(CHUNK)
        last = not nxt
        nonce = secrets.token_bytes(12)
        ct = aes.encrypt(nonce, chunk, _aad(index, last))
        dst.write(struct.pack(">I", len(ct)) + nonce + ct)
        if last:
            return
        chunk, index = nxt, index + 1


def decrypt_stream(src, dst, key: bytes) -> None:
    if src.read(len(MAGIC)) != MAGIC:
        raise OffsiteError("not an off-site backup file")
    aes = AESGCM(key)
    index = 0
    while True:
        head = src.read(4)
        if len(head) < 4:
            raise OffsiteError("the file is cut short")
        (size,) = struct.unpack(">I", head)
        nonce = src.read(12)
        ct = src.read(size)
        if len(nonce) < 12 or len(ct) < size:
            raise OffsiteError("the file is cut short")
        last = src.peek(1) == b"" if hasattr(src, "peek") else None
        if last is None:
            pos = src.tell()
            last = src.read(1) == b""
            src.seek(pos)
        try:
            dst.write(aes.decrypt(nonce, ct, _aad(index, last)))
        except InvalidTag as exc:
            raise OffsiteError("wrong key, or the file was changed or cut") from exc
        if last:
            return
        index += 1


def newest_dumps(dest: pathlib.Path) -> List[pathlib.Path]:
    out = []
    for folder in sorted(p for p in dest.iterdir() if p.is_dir()):
        dumps = sorted(folder.glob("*.dump"), key=lambda p: (p.stat().st_mtime, p.name))
        if dumps:
            out.append(dumps[-1])
    return out


def copy(dest: str, to: str, key_file: str, *, uploads: Optional[str] = None, keep: int = DEFAULT_KEEP,
         now: Optional[dt.datetime] = None) -> pathlib.Path:
    src = pathlib.Path(dest).expanduser()
    target = pathlib.Path(to).expanduser()
    if not src.is_dir():
        raise OffsiteError(f"backup folder {src} not found")
    dumps = newest_dumps(src)
    if not dumps:
        raise OffsiteError(f"no dumps under {src}: run the nightly backup first")
    key = load_or_create_key(pathlib.Path(key_file).expanduser())
    target.mkdir(parents=True, exist_ok=True)
    stamp = (now or dt.datetime.now()).strftime("%Y%m%d-%H%M%S")
    final = target / f"retailops-{stamp}{SUFFIX}"
    part = final.with_name(final.name + ".part")
    manifest = {"made_at": stamp, "dumps": [f"{d.parent.name}/{d.name}" for d in dumps],
                "uploads": bool(uploads)}
    buf = tempfile.TemporaryFile(dir=str(src))  # on local disk, never in memory
    with tarfile.open(fileobj=buf, mode="w") as tar:
        data = json.dumps(manifest, indent=1).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        for d in dumps:
            tar.add(str(d), arcname=f"dumps/{d.parent.name}/{d.name}")
        if uploads:
            up = pathlib.Path(uploads).expanduser()
            if up.is_dir():
                tar.add(str(up), arcname="uploads")
    buf.seek(0)
    try:
        with open(part, "wb") as fh:
            encrypt_stream(buf, fh, key)
            fh.flush()
            os.fsync(fh.fileno())
    finally:
        buf.close()
    part.replace(final)
    copies = sorted(target.glob(f"retailops-*{SUFFIX}"))
    for old in copies[: max(0, len(copies) - max(1, keep))]:
        old.unlink()
    return final


def open_copy(file: str, key_file: str, out: str) -> pathlib.Path:
    dst = pathlib.Path(out).expanduser()
    if dst.exists() and any(dst.iterdir()):
        raise OffsiteError(f"{dst} is not empty")
    dst.mkdir(parents=True, exist_ok=True)
    key = load_or_create_key(pathlib.Path(key_file).expanduser(), create=False)
    with tempfile.TemporaryFile(dir=str(dst)) as plain:
        with open(pathlib.Path(file).expanduser(), "rb") as fh:
            decrypt_stream(fh, plain, key)
        plain.seek(0)
        with tarfile.open(fileobj=plain, mode="r") as tar:
            tar.extractall(dst, filter="data")
    return dst


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("copy")
    c.add_argument("--dest", required=True)
    c.add_argument("--to", required=True)
    c.add_argument("--key-file", required=True)
    c.add_argument("--uploads")
    c.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    o = sub.add_parser("open")
    o.add_argument("--file", required=True)
    o.add_argument("--key-file", required=True)
    o.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "copy":
            path = copy(args.dest, args.to, args.key_file, uploads=args.uploads, keep=args.keep)
            print(f"offsite: wrote {path.name} ({path.stat().st_size} bytes)")
        else:
            print(f"offsite: opened into {open_copy(args.file, args.key_file, args.out)}")
    except OffsiteError as exc:
        print(f"offsite: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
