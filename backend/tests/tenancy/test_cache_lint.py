"""No new process-wide state (docs/PLATFORM_CONTRACT.md 6.5).

An AST scan of backend/app fails on any module-level mutable container,
lock / semaphore / event, ``lru_cache`` / ``cache``, or ``global`` statement
that is not made through app.tenancy (tenant_local / tenant_lock /
tenant_cache / tenant_state) or listed in
backend/app/tenancy/global_state_allowlist.txt (``<path>:<name>  # reason``).
A new cache or lock in a shared service must be per store - or say why not.
"""

import ast
import pathlib
from typing import Iterator, Set, Tuple

APP = pathlib.Path(__file__).resolve().parents[2] / "app"
ALLOWLIST = APP / "tenancy" / "global_state_allowlist.txt"

_MUTABLE_CALLS = {
    "dict", "list", "set", "bytearray", "OrderedDict", "defaultdict", "deque", "Counter", "Queue",
    "Lock", "RLock", "Semaphore", "BoundedSemaphore", "Event", "Condition", "local", "WeakValueDictionary",
    "WeakKeyDictionary", "WeakSet",
}
_CACHE_DECORATORS = {"lru_cache", "cache", "cached_property"}


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            return f.attr
    return ""


def _is_mutable(value: ast.AST) -> bool:
    if isinstance(value, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)):
        return True
    return _call_name(value) in _MUTABLE_CALLS


def _targets(node) -> Iterator[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for t in targets:
        if isinstance(t, ast.Name):
            yield t.id


def scan() -> Set[Tuple[str, str]]:
    found: Set[Tuple[str, str]] = set()
    for path in sorted(APP.rglob("*.py")):
        rel = path.relative_to(APP.parent).as_posix()
        if rel.startswith("app/tenancy/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None and _is_mutable(node.value):
                for name in _targets(node):
                    found.add((rel, name))
        for node in ast.walk(tree):
            if isinstance(node, ast.Global):
                for name in node.names:
                    found.add((rel, f"global {name}"))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    target = dec.func if isinstance(dec, ast.Call) else dec
                    dname = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                    if dname in _CACHE_DECORATORS:
                        found.add((rel, f"@{dname} {node.name}"))
    return found


def allowlist() -> Set[Tuple[str, str]]:
    out = set()
    for line in ALLOWLIST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entry, _, reason = line.partition("#")
        assert reason.strip(), f"allowlist entry without a reason: {line!r}"
        path, _, name = entry.strip().partition(":")
        out.add((path.strip(), name.strip()))
    return out


def test_no_new_process_wide_state():
    new = sorted(scan() - allowlist())
    assert not new, (
        "Module-level state that is not per store (use app.tenancy tenant_local / tenant_lock / "
        "tenant_cache, or add it to app/tenancy/global_state_allowlist.txt with a reason): "
        + ", ".join(f"{p}:{n}" for p, n in new)
    )


def test_allowlist_has_no_stale_entries():
    stale = sorted(allowlist() - scan())
    assert not stale, "Allowlisted but no longer in the code (remove them): " + ", ".join(
        f"{p}:{n}" for p, n in stale
    )
