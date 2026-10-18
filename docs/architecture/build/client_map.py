"""Map website pages and app screens to the API endpoints they call (static, from source).

Website: page -> transitive relative imports (never into another page) -> api.js functions -> (method, path).
App: screen -> imports (same feature, any providers/data/repository file; never another screen) ->
repository files -> each Dio call's (method, path).
"""
import json
import os
import re
import sys

W = "/Users/sundar/spicetown-dev/website/frontend/src"
M = "/Users/sundar/spicetown-dev/mobile/lib"


def read_literal(s, i):
    """s[i] is a quote/backtick; return (content with ${..} -> {}, end index)."""
    q = s[i]
    out, j = [], i + 1
    while j < len(s):
        c = s[j]
        if c == "\\":
            out.append(s[j : j + 2])
            j += 2
            continue
        if q == "`" and s.startswith("${", j):
            depth, k = 1, j + 2
            while k < len(s) and depth:
                if s[k] == "{":
                    depth += 1
                elif s[k] == "}":
                    depth -= 1
                elif s[k] == "`":  # nested template: skip it
                    _, k = read_literal(s, k)
                    continue
                k += 1
            out.append("{}")
            j = k
            continue
        if c == q:
            return "".join(out), j + 1
        out.append(c)
        j += 1
    return "".join(out), j


def clean_path(p):
    p = p.split("?")[0]
    p = re.sub(r"\{\}\{\}+", "{}", p)
    if p.endswith("{}") and not p.endswith("/{}"):
        p = p[:-2]  # a trailing ${qs} query string
    return p


# ---------------- website ----------------
api_src = open(os.path.join(W, "api.js")).read()
# Resolve module constants used as path prefixes: const FLYERS = "/api/flyers";
PATH_CONSTS = {}
for _m in re.finditer(r"^const (\w+) = [\"`'](/(?:api|public)/[^\"`']*)[\"`'];", api_src, re.M):
    PATH_CONSTS[_m.group(1)] = _m.group(2)
    api_src = api_src.replace("${" + _m.group(1) + "}", _m.group(2))
CALLS = {
    "request": None,
    "postJSON": "POST",
    "patchJSON": "PATCH",
    "putJSONChecklist": "PUT",
    "putJSONLeads": "PUT",
    "downloadFile": "GET",
    "publicRequest": None,
    "jsonRequest": "ARG0",
}
funcs = {}
blocks = re.split(r"\n(?=(?:export )?(?:async )?function |export const |const )", api_src)
for b in blocks:
    m = re.match(r"export (?:async )?(?:function\s+(\w+)|const\s+(\w+)\s*=)", b)
    if not m:
        continue
    name = m.group(1) or m.group(2)
    eps = set()
    for cm in re.finditer(r"\b(xhr\.open|request|postJSON|patchJSON|putJSONChecklist|putJSONLeads|downloadFile|publicRequest|jsonRequest|cartRemovalCall|getInsight)\(", b):
        fn = cm.group(1)
        k = cm.end()
        while b[k] in " \n":
            k += 1
        if fn == "getInsight":
            lit, _ = read_literal(b, k)
            eps.add(("GET", f"/api/insights/{lit}"))
            continue
        if fn == "cartRemovalCall":
            args = b[k : b.index(")", k)].split(",")
            meth = re.findall(r"\"(\w+)\"", b[k : b.index(")", k)])
            suffix = re.findall(r"\"(/[^\"]*)\"", b[k : b.index(")", k)])
            eps.add((meth[0] if meth else "POST", "/api/ordering/cart/items/{}/removal-request" + (suffix[0] if suffix else "")))
            continue
        method = CALLS.get(fn)
        if fn in ("jsonRequest", "xhr.open"):
            meth, k2 = read_literal(b, k)
            method = meth
            k = k2
            while b[k] in ", \n":
                k += 1
        ident = re.match(r"([A-Z_]+)\b", b[k:])
        if ident and ident.group(1) in PATH_CONSTS:
            path, end = PATH_CONSTS[ident.group(1)], k + len(ident.group(1))
        elif b[k] not in "`\"'":
            continue
        else:
            path, end = read_literal(b, k)
        if path.startswith("{}/"):
            path = path[2:]
        if not path.startswith(("/api", "/public")):
            continue
        if method is None:
            mm = re.match(r"\s*,\s*\{[^}]*?method:\s*([^,}]+)", b[end : end + 300], re.S)
            methods = re.findall(r"\"(GET|POST|PUT|PATCH|DELETE)\"", mm.group(1)) if mm else []
            for meth in methods or ["GET"]:
                eps.add((meth, clean_path(path)))
            continue
        eps.add((method, clean_path(path)))
    if eps:
        funcs[name] = sorted(f"{a} {p}" for a, p in eps)

CHROME = {"GET /api/auth/me", "POST /api/auth/login", "POST /api/auth/logout", "POST /api/auth/mobile-login", "GET /api/glossary"}


def js_imports(path):
    src = open(path).read()
    out = []
    for m in re.finditer(r"import\s+([^;]*?)\s+from\s+[\"'](\.[^\"']+)[\"']", src, re.S):
        spec, rel = m.group(1), m.group(2)
        base = os.path.normpath(os.path.join(os.path.dirname(path), rel))
        for ext in ("", ".jsx", ".js", "/index.js", "/index.jsx"):
            if os.path.isfile(base + ext):
                out.append((spec, base + ext))
                break
    return src, out


def web_closure(start):
    seen, stack, used = set(), [start], set()
    while stack:
        f = stack.pop()
        if f in seen:
            continue
        seen.add(f)
        src, imps = js_imports(f)
        for spec, target in imps:
            if target.endswith("/api.js"):
                used |= set(re.findall(r"\w+", re.sub(r"\bas\b", " ", spec)))
            elif ("/pages/" in target and target != start) or target.endswith(("AuthContext.jsx", "GlossaryContext.jsx", "Sidebar.jsx")):
                continue
            else:
                stack.append(target)
    eps = set()
    for u in used:
        eps |= set(funcs.get(u, []))
    return sorted(eps - CHROME), sorted(os.path.relpath(s, W) for s in seen)


web = {}
pages = []
for root, _, files in os.walk(os.path.join(W, "pages")):
    for fn in files:
        if fn.endswith(".jsx"):
            pages.append(os.path.join(root, fn))
for extra in ("components/CartButton.jsx", "components/ChatWidget.jsx", "components/Sidebar.jsx", "AuthContext.jsx", "GlossaryContext.jsx"):
    if os.path.exists(os.path.join(W, extra)):
        pages.append(os.path.join(W, extra))
for p in sorted(pages):
    eps, files = web_closure(p)
    if p.endswith(("AuthContext.jsx", "GlossaryContext.jsx")):
        src, _ = js_imports(p)
        names = set(re.findall(r"\b(\w+)\(", src))
        eps = sorted(set(e for n in names for e in funcs.get(n, [])))
    web[os.path.relpath(p, W)] = {"endpoints": eps, "files": files}

# ---------------- app ----------------
cfg = open(os.path.join(M, "core/api/api_config.dart")).read()
raw_consts = {}
for m in re.finditer(r"static\s+(?:const|String)\s+(\w+)\s*(?:\([^)]*\))?\s*(?:=>|=)\s*'([^']+)'", cfg, re.S):
    raw_consts[m.group(1)] = m.group(2)


def _expand(v, depth=0):
    # ${otherConst(...)} / ${otherConst} -> that constant's path; any other ${...} / $x -> {}
    def sub(mm):
        name = mm.group(1)
        if name in raw_consts and depth < 4:
            return _expand(raw_consts[name], depth + 1)
        return "{}"
    v = re.sub(r"\$\{(\w+)(?:\([^)]*\))?\}", sub, v)
    return re.sub(r"\$\{[^}]*\}|\$\w+", "{}", v)


consts = {k: _expand(v) for k, v in raw_consts.items()}


def resolve_dart(expr, local):
    expr = expr.strip()
    m = re.fullmatch(r"ApiConfig\.(\w+)(?:\([^)]*\))?", expr)
    if m:
        return consts.get(m.group(1))
    if re.fullmatch(r"_?\w+", expr) and expr in local:
        return local[expr]
    m = re.fullmatch(r"'([^']*)'", expr, re.S)
    if m:
        s = m.group(1)
        s = re.sub(r"\$\{ApiConfig\.(\w+)(?:\([^)]*\))?\}", lambda mm: consts.get(mm.group(1), "{?}"), s)
        s = re.sub(r"\$\{Uri\.encodeComponent\([^)]*\)\}", "{}", s)
        s = re.sub(r"\$\{_\w+\((\w+)\)\}", "/api/checklists/admin/definitions/{}", s)
        s = re.sub(r"\$\{(\w+)\}", lambda mm: local.get(mm.group(1), "{}"), s)
        s = re.sub(r"\$\{[^}]*\}|\$\w+", "{}", s)
        return s
    return None


def repo_calls(path):
    src = open(path).read()
    local = {}
    for m in re.finditer(r"(?:static\s+)?(?:const|final)\s+(?:String\s+)?(_?\w+)\s*=\s*'(/api/[^']*)'", src):
        local[m.group(1)] = m.group(2)
    eps = set()
    for m in re.finditer(r"_?(?:api|dio|client)\.(get|post|put|patch|delete|upload\w*|download\w*|getBytes|postMultipart|postBytes)(?:<[^>(]*(?:<[^>]*>)?[^>(]*>)?\(", src):
        k = m.end()
        depth, j = 0, k
        while j < len(src):
            c = src[j]
            if c in "([{":
                depth += 1
            elif c in ")]}":
                if depth == 0:
                    break
                depth -= 1
            elif c == "," and depth == 0:
                break
            elif c == "'":
                _, j = read_literal(src, j)
                continue
            j += 1
        expr = src[k:j]
        # adjacent string literals: 'a' 'b'
        expr = re.sub(r"'\s*\n\s*'", "", expr)
        p = resolve_dart(expr, local)
        verb = m.group(1)
        meth = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "delete": "DELETE"}.get(verb, "POST" if verb.startswith(("upload", "post")) else "GET")
        if p:
            eps.add(f"{meth} {clean_path(p)}")
    if not eps:  # fall back: constants referenced
        for c in re.findall(r"ApiConfig\.(\w+)", src):
            if c in consts:
                eps.add(f"? {consts[c]}")
    return sorted(eps)


def dart_imports(path):
    src = open(path).read()
    out = []
    for m in re.finditer(r"import\s+'([^']+)'", src):
        u = m.group(1)
        if u.startswith("package:spicetown_app/"):
            out.append(os.path.join(M, u[len("package:spicetown_app/") :]))
        elif not u.startswith(("package:", "dart:")):
            out.append(os.path.normpath(os.path.join(os.path.dirname(path), u)))
    return src, [o for o in out if os.path.isfile(o)]


def feature_of(path):
    parts = os.path.relpath(path, M).split(os.sep)
    if parts[0] == "features" and parts[1] == "team" and len(parts) > 3:
        return "/".join(parts[:3])
    return "/".join(parts[:2])


repo_eps = {}


def app_closure(start):
    feat = feature_of(start)
    seen, stack, repos, provs = set(), [(start, 0)], set(), set()
    while stack:
        f, depth = stack.pop(0)
        if f in seen or depth > 5:
            continue
        seen.add(f)
        _, imps = dart_imports(f)
        rel_f = os.path.relpath(f, M)
        if "/providers/" in rel_f or rel_f.endswith("_providers.dart"):
            provs.add(rel_f)
        if rel_f.endswith("_repository.dart") or rel_f.endswith("labels_api_client.dart"):
            repos.add(rel_f)
            if rel_f not in repo_eps:
                repo_eps[rel_f] = repo_calls(f)
            continue
        for t in imps:
            rel = os.path.relpath(t, M)
            if rel.startswith(("app/", "features/search/", "core/api/")) or rel.endswith("feature_index.dart"):
                continue
            if "/screens/" in rel and t != start:
                continue
            same = feature_of(t) == feat
            here_same = feature_of(f) == feat
            # Leave the screen's own feature only into a provider/data file it (or
            # its feature) imports directly; from there, only into repositories.
            if same or rel.endswith("_repository.dart") or (here_same and ("/providers/" in rel or "/data/" in rel)):
                stack.append((t, depth + 1))
    return sorted(repos), sorted(provs)


app = {}
for root, _, files in os.walk(os.path.join(M, "features")):
    for fn in files:
        if "/screens" in root and fn.endswith(".dart"):
            p = os.path.join(root, fn)
            repos, provs = app_closure(p)
            app[os.path.relpath(p, M)] = {"repos": repos, "providers": provs}
for p in ("core/glossary/glossary_screen.dart",):
    repos, provs = app_closure(os.path.join(M, p))
    app[p] = {"repos": repos, "providers": provs}
# every repository, even unreached
for root, _, files in os.walk(M):
    for fn in files:
        if fn.endswith("_repository.dart"):
            rel = os.path.relpath(os.path.join(root, fn), M)
            repo_eps.setdefault(rel, repo_calls(os.path.join(root, fn)))

json.dump({"web": web, "web_funcs": funcs, "app": app, "repo_eps": repo_eps, "app_consts": consts}, open(sys.argv[1], "w"), indent=1)
print(len(web), "web pages", len(funcs), "api fns", len(app), "app screens", len(repo_eps), "repos")
