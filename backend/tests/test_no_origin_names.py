"""Name guard (KloudC RetailOps final pass; was the M2 contract 9.2 name
test): no tracked file in this repository - code, data, tests, docs, packs,
fixtures, comments, deploy files - names the store the product was first
built for, its kitchen, its town, street address, zip, phone, private host or
its staff.

The words are matched by hash (tests/origin_names.py), so this file and the
allowlist carry no names. The only allowed hit is the blocked-domain
constant: the product refuses to serve from that domain (rule 0.3), so the
domain must be written once in the code. Allowlist entries are
``<path>:<substring of the line>`` and must still match something.
"""

import pathlib
import re
import subprocess
from typing import Dict, List, Set, Tuple

import pytest

from tests.origin_names import find_origin_names, has_origin_name

REPO = pathlib.Path(__file__).resolve().parents[2]
ALLOWLIST = pathlib.Path(__file__).resolve().parent / "data" / "origin_name_allowlist.txt"
# Private extra words (never committed); checked when the file is on this
# machine (tests/store_fixtures.py PRIVATE_DIR).
from tests.store_fixtures import PRIVATE_DIR  # noqa: E402

PRIVATE_TOKENS = PRIVATE_DIR / "forbidden_tokens.txt"

SKIP_PARTS = {"node_modules", "__pycache__", ".pytest_cache", "build", "dist", "Pods", ".gradle", ".venv"}


def _tracked() -> List[pathlib.Path]:
    """Every file git tracks (or, without git, every file under the repo)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True, check=True, timeout=60
        ).stdout.decode("utf-8")
        paths = [REPO / p for p in out.split("\0") if p]
    except (OSError, subprocess.SubprocessError):
        paths = [p for p in REPO.rglob("*") if p.is_file()]
    return [p for p in paths if p.is_file() and not (set(p.relative_to(REPO).parts) & SKIP_PARTS)]


def _text(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""  # binary (images, fonts, jars)


def _rel(path: pathlib.Path) -> str:
    return path.relative_to(REPO).as_posix()


def _load_allowlist() -> Dict[str, List[str]]:
    entries: Dict[str, List[str]] = {}
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        path, sep, substring = raw.partition(":")
        assert sep and substring, f"bad allowlist line (want <path>:<substring>): {raw!r}"
        entries.setdefault(path, []).append(substring)
    return entries


def test_the_matcher_works():
    # Neutral sample data must never be a hit.
    for ok in ("Main Store", "Market Kitchen", "Windsor, CT 06095", "(860) 555-0142", "KloudC RetailOps", "Sasha"):
        assert not has_origin_name(ok), ok
    # Phone number shapes are joined across the punctuation.
    phone = ["86" + "0", "23" + "7", "42" + "80"]
    assert has_origin_name("call " + "-".join(phone)) and has_origin_name("({}) {}-{}".format(*phone))


def test_no_tracked_file_names_the_origin_store():
    allow = _load_allowlist()
    used: Set[Tuple[str, str]] = set()
    hits = []
    files = _tracked()
    assert len(files) > 500, "the scan found too few files - did the repository layout change?"
    for p in files:
        rel = _rel(p)
        for n, line in find_origin_names(_text(p)):
            match = [s for s in allow.get(rel, []) if s in line]
            if match:
                used.update((rel, s) for s in match)
            else:
                hits.append(f"{rel}:{n}: {line.strip()[:160]}")
    stale = [f"{path}:{s}" for path, subs in allow.items() for s in subs if (path, s) not in used]
    msg = []
    if hits:
        msg.append("origin-store names in tracked files (use neutral sample data):\n" + "\n".join(hits))
    if stale:
        msg.append("allowlist entries that no longer match anything (delete them):\n" + "\n".join(stale))
    assert not msg, "\n\n".join(msg)


def test_the_allowlist_is_only_the_blocked_domain():
    allow = _load_allowlist()
    assert allow == {"backend/app/config.py": ['REFUSED_DOMAIN = "']}, allow


# Recorded business data of the origin store stays in the private folder.
ORIGIN_DATA_FILES = ("checklist_real_answers_", "prod_category_combos_", "legacy_cases", "origin.pack", "origin.env")


def test_no_origin_store_data_files_in_the_repository():
    found = sorted(_rel(p) for p in _tracked() if p.name.startswith(ORIGIN_DATA_FILES))
    assert not found, f"origin-store data files belong in the private folder: {found}"


def test_no_tracked_file_has_the_private_staff_names():
    """The private forbidden_tokens.txt (never committed) on top of the
    hashed list. Skips where the private file is absent (CI)."""
    if not PRIVATE_TOKENS.is_file():
        pytest.skip("private forbidden_tokens.txt not on this machine")
    tokens = [t.strip() for t in PRIVATE_TOKENS.read_text(encoding="utf-8").splitlines() if t.strip()]
    # A word on its own; a home folder path (/Users/<name>/) is not a staff name.
    words = re.compile(r"(?<![/\w])(?:" + "|".join(re.escape(t) for t in tokens) + r")\b(?!')", re.IGNORECASE)
    found = []
    for p in _tracked():
        for n, line in enumerate(_text(p).splitlines(), 1):
            if words.search(line):
                found.append(f"{_rel(p)}:{n}")  # the line itself is not printed (private)
    assert not found, "private staff names in tracked files:\n" + "\n".join(found)
