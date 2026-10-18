"""Name grep test (M2 contract 9.2): the product must not carry the legacy
origin store's names in its shell, identity or code.

Two scopes:

* IDENTITY files (product shell, deploy files, docs for developers) must have
  NO hit at all - the allowlist does not apply to them.
* CODE (backend/app, frontend/src) may only hit on lines listed in
  tests/data/spicetown_name_allowlist.txt (`<path>:<exact substring>`). M2
  batches MT7, MT8 and MT9 shrink that list as they move store values into
  settings and the store pack. An allowlist entry that no longer matches
  anything fails too, so the list can only shrink.

Explicitly NOT searched (store-pack data and migration history):
* backend/alembic/versions/ - already-applied migrations insert legacy rows;
  `python -m app.store init` (MT8) removes them for every new store.
* backend/app/services/checklist_builtin.py - the legacy built-in checklist
  data, replaced by the store pack's checklists (MT8).
* backend/packs/ - store-pack data files.
"""

import os
import pathlib
import re
from typing import Dict, List, Set, Tuple

REPO = pathlib.Path(__file__).resolve().parents[2]
ALLOWLIST = pathlib.Path(__file__).resolve().parent / "data" / "spicetown_name_allowlist.txt"

# The contract's pattern plus the one legacy supplier name the old migrations
# seed (the fresh-store test's list, contract 3.6).
PATTERN = re.compile(r"spice ?town|street kitchen|vernon|spicetown\.shop|shaz international", re.IGNORECASE)
# Private extra words (staff names, contract 1.6) - never committed; checked
# when the file is on this machine.
PRIVATE_TOKENS = pathlib.Path(
    os.environ.get("STOREOPS_PRIVATE_DIR") or "/Users/sundar/.claude/jobs/e26a1611/tmp/multitenant/m2/private"
) / "forbidden_tokens.txt"

# Product shell and identity: zero hits, no allowlist.
IDENTITY_FILES = [
    "README.md",
    "Dockerfile",
    ".dockerignore",
    "docker-compose.yml",
    ".env.example",
    "frontend/index.html",
    "frontend/capacitor.config.json",
    "frontend/package.json",
    "frontend/.env.mobile",
    "frontend/src/pages/LoginPage.jsx",
]
IDENTITY_DIRS = [
    ".claude",
    ".github",
    "docker",
    "frontend/public",
    "frontend/android",
    "frontend/ios",
    "backend/app/assets/branding",
    "backend/scripts/launchd",
    "backend/scripts/backup",
    "backend/scripts/branding",
]

# Code: hits only on allowlisted lines.
CODE_DIRS = ["backend/app", "frontend/src"]
CODE_FILES: List[str] = []

EXCLUDED = {
    "backend/app/services/checklist_builtin.py": "legacy built-in checklist data (store pack, MT8)",
}
EXCLUDED_DIRS = {
    "backend/alembic/versions": "migration history (store-init removes the legacy rows, MT8)",
    "backend/packs": "store-pack data",
}

SKIP_PARTS = {"node_modules", "__pycache__", ".pytest_cache", "build", "dist", "Pods", ".gradle"}


def _rel(path: pathlib.Path) -> str:
    return path.relative_to(REPO).as_posix()


def _excluded(rel: str) -> bool:
    if rel in EXCLUDED:
        return True
    return any(rel == d or rel.startswith(d + "/") for d in EXCLUDED_DIRS)


def _files(dirs: List[str], files: List[str]) -> List[pathlib.Path]:
    out = []
    for f in files:
        p = REPO / f
        if p.is_file():
            out.append(p)
    for d in dirs:
        base = REPO / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file() and not (set(p.relative_to(REPO).parts) & SKIP_PARTS):
                out.append(p)
    return [p for p in out if not _excluded(_rel(p))]


def _hits(path: pathlib.Path) -> List[Tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []  # binary (images, jars)
    return [(n, line) for n, line in enumerate(text.splitlines(), 1) if PATTERN.search(line)]


def _load_allowlist() -> Dict[str, List[str]]:
    entries: Dict[str, List[str]] = {}
    for raw in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.startswith("#"):
            continue
        path, sep, substring = raw.partition(":")
        assert sep and substring, f"bad allowlist line (want <path>:<substring>): {raw!r}"
        entries.setdefault(path, []).append(substring)
    return entries


def test_identity_files_have_no_legacy_store_names():
    found = []
    for p in _files(IDENTITY_DIRS, IDENTITY_FILES):
        for n, line in _hits(p):
            found.append(f"{_rel(p)}:{n}: {line.strip()[:160]}")
    assert not found, "legacy store names in product identity files:\n" + "\n".join(found)


def test_identity_scope_exists():
    # Guard against the test silently passing because paths moved.
    for f in ("README.md", "Dockerfile", "docker-compose.yml", "frontend/index.html", "frontend/capacitor.config.json"):
        assert (REPO / f).is_file(), f
    assert len(_files(IDENTITY_DIRS, IDENTITY_FILES)) > 20


def test_code_hits_are_all_allowlisted_and_the_allowlist_has_no_stale_entries():
    allow = _load_allowlist()
    used: Set[Tuple[str, str]] = set()
    unlisted = []
    for p in _files(CODE_DIRS, CODE_FILES):
        rel = _rel(p)
        for n, line in _hits(p):
            match = [s for s in allow.get(rel, []) if s in line]
            if match:
                used.update((rel, s) for s in match)
            else:
                unlisted.append(f"{rel}:{n}: {line.strip()[:160]}")
    stale = [f"{path}:{s}" for path, subs in allow.items() for s in subs if (path, s) not in used]
    msg = []
    if unlisted:
        msg.append(
            "legacy store names outside the allowlist (move the value into a setting or the store pack):\n"
            + "\n".join(unlisted)
        )
    if stale:
        msg.append("allowlist entries that no longer match anything (delete them):\n" + "\n".join(stale))
    assert not msg, "\n\n".join(msg)


def test_explicit_exclusions_still_exist():
    """An exclusion for a file that is gone hides nothing and should be removed."""
    for rel in EXCLUDED:
        assert (REPO / rel).is_file(), f"EXCLUDED entry no longer exists: {rel}"
    assert (REPO / "backend/alembic/versions").is_dir()


# The origin store's production folder and its recorded business data stay
# out of the product: no code or test may name that folder, and no origin
# data file may sit in the repository's test data (private folder only,
# M2 contract 1.6).
ORIGIN_FOLDER = re.compile(r"/spicetown-backend\b")
ORIGIN_DATA_FILES = ("checklist_real_answers_", "prod_category_combos_")


def test_code_and_tests_never_reach_into_the_origin_store_folder():
    found = []
    for p in _files(["backend/app", "backend/tests", "backend/alembic"], []):
        if p.resolve() == pathlib.Path(__file__).resolve():
            continue
        for n, line in enumerate(_text(p).splitlines(), 1):
            if ORIGIN_FOLDER.search(line):
                found.append(f"{_rel(p)}:{n}: {line.strip()[:160]}")
    assert not found, "origin store folder named in code or tests:\n" + "\n".join(found)


def test_no_origin_store_data_files_in_the_repository():
    data = REPO / "backend" / "tests" / "data"
    found = sorted(p.name for p in data.iterdir() if p.name.startswith(ORIGIN_DATA_FILES)) if data.is_dir() else []
    assert not found, f"origin-store data files belong in the private folder: {found}"


def _text(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def test_code_has_none_of_the_private_staff_names():
    """The legacy store's staff names (private forbidden_tokens.txt) appear
    nowhere in backend/app or frontend/src - not even in comments or test
    data. Skips where the private file is absent (CI)."""
    import pytest

    if not PRIVATE_TOKENS.is_file():
        pytest.skip("private forbidden_tokens.txt not on this machine")
    tokens = [t.strip() for t in PRIVATE_TOKENS.read_text(encoding="utf-8").splitlines() if t.strip()]
    words = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in tokens) + r")\b(?!')", re.IGNORECASE)
    found = []
    for p in _files(CODE_DIRS, CODE_FILES):
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if words.search(line):
                found.append(f"{_rel(p)}:{n}")  # the line itself is not printed (private)
    assert not found, "private staff names in code:\n" + "\n".join(found)
