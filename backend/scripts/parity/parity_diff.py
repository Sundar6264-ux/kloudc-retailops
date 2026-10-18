"""Compare two parity recordings (parity_record.py output).

    .venv/bin/python scripts/parity/parity_diff.py BASELINE_DIR NEW_DIR [--json report.json]

Exit code 0 = no differences, 1 = differences, 2 = usage error.

What counts:
* DIFFERENCE - a value changed, a JSON key or list element disappeared, a
  value's JSON type changed (3 -> 3.0 counts: the app's Dart models cast
  `json['id'] as int`, which throws on a double), the order of the keys an
  object already had changed (the website lists some objects - Fix Issues
  by_reason / by_type, checklist schemas - in key order), an endpoint's
  status code, content type or download file name changed, an
  endpoint/table/column in the baseline is missing from the new recording, a
  table's row count or a column's checksum changed (before the calls, and
  after them: read endpoints must not write), any change at all in the push
  alerts or the scheduled job list.
* ADDITION (allowed, listed but not a difference) - M1's golden rule allows
  additive API changes only: a new JSON key in an endpoint answer, a new
  table or column in the database. Use --strict to count these as
  differences too. Additions are never allowed in alerts.json or
  scheduler_jobs.json: a new push or a new job is a behaviour change.
* NOT COMPARED - an endpoint recorded only in the new recording (the
  endpoint list grew); re-record the baseline to cover it.

Ignored on purpose (each one is inherently time-varying or not business
output; see IGNORED below for the per-field reasons): meta.json as a whole
(timings, wall clock, git commit, Python version) - its "now", "user" and
"top_vendors" are checked separately because a recording made with a
different fixed now or user is not comparable at all.
"""

import argparse
import fnmatch
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional, Tuple

# (file glob, JSON path glob, reason). Paths look like
# "json.items[3].name"; '*' matches anything, including dots and brackets.
# Every entry needs a reason a reviewer can check; keep this list short.
IGNORED: List[Tuple[str, str, str]] = [
    # Nothing yet: two recordings of the same copy came out identical with no
    # ignores (fixed "now", PYTHONHASHSEED=0, background threads not run).
    # Add an entry only with a reason a reviewer can check.
]

MAX_PRINT_PER_FILE = 15


class Report:
    def __init__(self) -> None:
        self.differences: List[Dict[str, Any]] = []
        self.additions: List[Dict[str, Any]] = []
        self.not_compared: List[str] = []
        self.notes: List[str] = []
        self.compared_files = 0

    def diff(self, where: str, path: str, a: Any, b: Any, what: str = "changed") -> None:
        self.differences.append({"file": where, "path": path, "what": what, "a": _short(a), "b": _short(b)})

    def add(self, where: str, path: str, b: Any) -> None:
        self.additions.append({"file": where, "path": path, "b": _short(b)})


def _short(value: Any) -> Any:
    text = json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)
    return value if len(text) <= 200 else text[:197] + "..."


def _ignored(file: str, path: str) -> bool:
    return any(fnmatch.fnmatchcase(file, f) and fnmatch.fnmatchcase(path, p) for f, p, _ in IGNORED)


def compare_values(
    report: Report, where: str, path: str, a: Any, b: Any, allow_additions: bool, check_order: bool = False
) -> None:
    if _ignored(where, path):
        return
    if isinstance(a, dict) and isinstance(b, dict):
        if check_order:
            # Keys both sides have, in each side's order: a new key anywhere
            # is an addition, not a reorder.
            order_a = [k for k in a if k in b]
            order_b = [k for k in b if k in a]
            if order_a != order_b:
                report.diff(where, path or "(top)", order_a, order_b, "key order changed")
        for key in a:
            sub = f"{path}.{key}" if path else str(key)
            if key not in b:
                if not _ignored(where, sub):
                    report.diff(where, sub, a[key], None, "missing in new")
            else:
                compare_values(report, where, sub, a[key], b[key], allow_additions, check_order)
        for key in b:
            if key not in a:
                sub = f"{path}.{key}" if path else str(key)
                if _ignored(where, sub):
                    continue
                if allow_additions:
                    report.add(where, sub, b[key])
                else:
                    report.diff(where, sub, None, b[key], "added")
        return
    if isinstance(a, list) and isinstance(b, list):
        for i in range(min(len(a), len(b))):
            compare_values(report, where, f"{path}[{i}]", a[i], b[i], allow_additions, check_order)
        if len(a) != len(b):
            report.diff(where, f"{path}.length", len(a), len(b), "list length")
        return
    # Exact JSON types: True == 1 and 3 == 3.0 in Python, but not to the
    # app (bool vs number, and `as int` / `as double` casts in Dart).
    if type(a) is not type(b):
        report.diff(where, path, a, b, "type changed")
    elif a != b:
        report.diff(where, path, a, b)


def _load(path: pathlib.Path) -> Any:
    return json.loads(path.read_text())


def compare_endpoints(
    report: Report, a_dir: pathlib.Path, b_dir: pathlib.Path, strict: bool, check_order: bool = False
) -> None:
    a_files = {p.name for p in (a_dir / "endpoints").glob("*.json")}
    b_files = {p.name for p in (b_dir / "endpoints").glob("*.json")}
    for name in sorted(a_files - b_files):
        report.diff(f"endpoints/{name}", "", "recorded", None, "endpoint missing in new")
    for name in sorted(b_files - a_files):
        report.not_compared.append(f"endpoints/{name}")
    for name in sorted(a_files & b_files):
        where = f"endpoints/{name}"
        a, b = _load(a_dir / "endpoints" / name), _load(b_dir / "endpoints" / name)
        report.compared_files += 1
        if a.get("status") != b.get("status"):
            report.diff(where, "status", a.get("status"), b.get("status"))
        if a.get("params") != b.get("params"):
            report.diff(where, "params", a.get("params"), b.get("params"), "called differently")
        for header in ("content_type", "content_disposition"):
            # content_disposition is only in format 2 recordings.
            if header in a and a.get(header) != b.get(header):
                report.diff(where, header, a.get(header), b.get(header))
        for body in ("json", "sha256", "bytes"):
            if body in a or body in b:
                compare_values(
                    report, where, body, a.get(body), b.get(body), allow_additions=not strict, check_order=check_order
                )
        if "text" in a or "text" in b:
            # CSV and other text answers line by line, so a report shows the
            # line that changed instead of two long strings.
            lines_a = a["text"].splitlines() if isinstance(a.get("text"), str) else a.get("text")
            lines_b = b["text"].splitlines() if isinstance(b.get("text"), str) else b.get("text")
            compare_values(report, where, "text_lines", lines_a, lines_b, allow_additions=False)


def compare_fingerprints(report: Report, a: Dict, b: Dict, strict: bool, where: str = "fingerprint.json") -> None:
    report.compared_files += 1
    at, bt = a.get("tables", {}), b.get("tables", {})
    for table in sorted(at):
        if table not in bt:
            report.diff(where, f"tables.{table}", "present", None, "table missing in new")
            continue
        ta, tb = at[table], bt[table]
        if ta["rows"] != tb["rows"]:
            report.diff(where, f"tables.{table}.rows", ta["rows"], tb["rows"], "row count")
        for col, digest in ta["columns"].items():
            if col not in tb["columns"]:
                report.diff(where, f"tables.{table}.{col}", "present", None, "column missing in new")
            elif tb["columns"][col] != digest:
                report.diff(where, f"tables.{table}.{col}", digest[:12], tb["columns"][col][:12], "column checksum")
        for col in tb["columns"]:
            if col not in ta["columns"]:
                (report.diff(where, f"tables.{table}.{col}", None, "new column", "added") if strict
                 else report.add(where, f"tables.{table}.{col}", "new column"))
    for table in sorted(set(bt) - set(at)):
        (report.diff(where, f"tables.{table}", None, "new table", "added") if strict
         else report.add(where, f"tables.{table}", f"new table ({bt[table]['rows']} rows)"))


def _minor(version: Optional[str]) -> Optional[str]:
    return ".".join(str(version).split(".")[:2]) if version else None


def _jobs_by_id(jobs: Dict) -> Dict:
    """{scheduler: {job id: job}} so one added or removed job is reported as
    exactly that, not as every later job shifting by one."""
    return {
        label: ({j["id"]: j for j in items} if isinstance(items, list) else items) for label, items in jobs.items()
    }


def compare(a_dir: pathlib.Path, b_dir: pathlib.Path, strict: bool = False) -> Report:
    report = Report()
    meta_a, meta_b = _load(a_dir / "meta.json"), _load(b_dir / "meta.json")
    for key in ("now", "user", "top_vendors"):
        if meta_a.get(key) != meta_b.get(key):
            report.diff("meta.json", key, meta_a.get(key), meta_b.get(key), "recordings not comparable")
    # Python 3.12's sum() adds floats with compensated summation, so 3.9 and
    # 3.12 round a few forecast numbers a cent apart (measured on the Oct 15
    # copy: 23 values). Compare recordings made by the same Python only.
    py_a, py_b = _minor(meta_a.get("python")), _minor(meta_b.get("python"))
    if py_a != py_b:
        report.diff("meta.json", "python", py_a, py_b, "recordings not comparable (record both with one Python)")
    # Format 1 recordings wrote endpoint answers with sorted keys, so key
    # order can only be checked between two format 2 recordings.
    check_order = min(int(meta_a.get("format_version") or 1), int(meta_b.get("format_version") or 1)) >= 2
    if not check_order:
        report.notes.append("key order not checked (a format 1 recording; re-record the baseline)")
    compare_endpoints(report, a_dir, b_dir, strict, check_order)
    compare_fingerprints(report, _load(a_dir / "fingerprint.json"), _load(b_dir / "fingerprint.json"), strict)
    # The database after the calls (format 2): a read endpoint that starts
    # writing shows up here.
    if (a_dir / "fingerprint_after.json").is_file():
        if (b_dir / "fingerprint_after.json").is_file():
            compare_fingerprints(
                report,
                _load(a_dir / "fingerprint_after.json"),
                _load(b_dir / "fingerprint_after.json"),
                strict,
                where="fingerprint_after.json",
            )
        else:
            report.diff("fingerprint_after.json", "", "recorded", None, "missing in new")
    report.compared_files += 1
    compare_values(report, "alerts.json", "", _load(a_dir / "alerts.json"), _load(b_dir / "alerts.json"), False)
    report.compared_files += 1
    compare_values(
        report,
        "scheduler_jobs.json",
        "",
        _jobs_by_id(_load(a_dir / "scheduler_jobs.json")),
        _jobs_by_id(_load(b_dir / "scheduler_jobs.json")),
        allow_additions=False,
    )
    return report


def render(report: Report, a_dir: pathlib.Path, b_dir: pathlib.Path) -> str:
    lines = [f"Parity diff: {a_dir}  vs  {b_dir}", f"Files compared: {report.compared_files}"]
    by_file: Dict[str, List[Dict]] = {}
    for d in report.differences:
        by_file.setdefault(d["file"], []).append(d)
    if by_file:
        lines.append(f"DIFFERENCES: {len(report.differences)} in {len(by_file)} file(s)")
        for file, items in sorted(by_file.items()):
            lines.append(f"  {file}: {len(items)}")
            for d in items[:MAX_PRINT_PER_FILE]:
                lines.append(f"    {d['path'] or '(whole file)'} [{d['what']}]: {d['a']!r} -> {d['b']!r}")
            if len(items) > MAX_PRINT_PER_FILE:
                lines.append(f"    ... {len(items) - MAX_PRINT_PER_FILE} more (use --json for all)")
    else:
        lines.append("DIFFERENCES: 0")
    if report.additions:
        lines.append(f"Additions (allowed in M1, not differences): {len(report.additions)}")
        for a in report.additions[:40]:
            lines.append(f"  + {a['file']} {a['path']}")
        if len(report.additions) > 40:
            lines.append(f"  ... {len(report.additions) - 40} more")
    if report.not_compared:
        lines.append(f"Only in the new recording (not compared): {', '.join(report.not_compared)}")
    for note in report.notes:
        lines.append(f"Note: {note}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("baseline")
    p.add_argument("new")
    p.add_argument("--strict", action="store_true", help="Count additions (new keys/columns/tables) as differences")
    p.add_argument("--json", help="Also write the full report as JSON here")
    args = p.parse_args(argv)
    a_dir, b_dir = pathlib.Path(args.baseline), pathlib.Path(args.new)
    for d in (a_dir, b_dir):
        if not (d / "meta.json").is_file():
            print(f"Not a recording (no meta.json): {d}", file=sys.stderr)
            return 2
    report = compare(a_dir, b_dir, strict=args.strict)
    print(render(report, a_dir, b_dir))
    if args.json:
        pathlib.Path(args.json).write_text(
            json.dumps(
                {"differences": report.differences, "additions": report.additions, "not_compared": report.not_compared},
                indent=1,
                default=str,
            )
        )
    return 1 if report.differences else 0


if __name__ == "__main__":
    sys.exit(main())
