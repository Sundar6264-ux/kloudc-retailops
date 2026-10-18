"""Store directory (M2 contract section 4, MT10): the FastAPI service, the
static build, the Worker (run under Node, compared with the service) and the
association files on store servers. Only the fake stores; no network."""

import json
import os
import pathlib
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.directory import build_static
from app.directory.config import DirectoryConfig, DirectoryConfigError, is_forbidden_host
from app.directory.main import create_app
from app.directory.open_page import open_page
from app.directory.stores import (
    StoresFileError,
    compact_json,
    etag_for,
    load_stores_file,
    normalize_code,
    parse_stores,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "directory" / "stores.example.json"
WORKER_DIR = REPO / "directory" / "worker"
TEAM = "ABCDE12345"
FP = ":".join(["AB"] * 32)
FAKE_CODES = ["STOREOPS_TEST_TOAST", "STOREOPS_TEST_CLOVER", "DEMO"]


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _example() -> dict:
    return json.loads(EXAMPLE.read_text())


def _write_stores(path: pathlib.Path, data: dict, mtime: int = 1_792_281_600) -> pathlib.Path:
    path.write_text(json.dumps(data))
    os.utime(path, (mtime, mtime))  # 2026-10-18T00:00:00Z
    return path


@pytest.fixture
def stores_file(tmp_path):
    return _write_stores(tmp_path / "stores.json", _example())


def _cfg(stores_file, **kw) -> DirectoryConfig:
    base = dict(stores_file=str(stores_file), client_ip_header="CF-Connecting-IP")
    base.update(kw)
    return DirectoryConfig(**base)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def directory(stores_file, clock):
    return TestClient(create_app(_cfg(stores_file, rate_per_minute=1000, rate_per_hour=10000), clock=clock))


def _get(client, path, ip="203.0.113.1", **headers):
    return client.get(path, headers={"CF-Connecting-IP": ip, **headers})


# --- the stores file -------------------------------------------------------


def test_example_file_holds_the_three_fake_stores_on_fake_hosts():
    stores = load_stores_file(EXAMPLE, DirectoryConfig())
    assert list(stores.stores) == FAKE_CODES
    for rec in stores.stores.values():
        assert rec.api_base_url.endswith(".storeops.invalid")
        assert rec.status == "active"
    toast = stores.stores["STOREOPS_TEST_TOAST"]
    assert dict(toast.branding)["timezone"] == "America/Chicago"
    assert dict(stores.stores["STOREOPS_TEST_CLOVER"].branding)["primary_color"] == "#15803D"
    assert dict(stores.stores["DEMO"].branding)["timezone"] == "America/New_York"


def _problems(data, cfg=None):
    with pytest.raises(StoresFileError) as exc:
        parse_stores(data, cfg or DirectoryConfig())
    return " | ".join(exc.value.problems)


def _one(**changes):
    data = _example()
    store = data["stores"][0]
    for key, value in changes.items():
        if key.startswith("branding__"):
            store["branding"][key[len("branding__") :]] = value
        elif value is ...:
            del store[key]
        else:
            store[key] = value
    return data


@pytest.mark.parametrize(
    "changes, expect",
    [
        ({"api_base_url": "https://store.example.com"}, "not under .storeops.invalid"),
        ({"api_base_url": "http://storeops-test-toast.storeops.invalid"}, "https://"),
        ({"api_base_url": "https://storeops-test-toast.storeops.invalid/api"}, "no path"),
        ({"api_base_url": "https://storeops-test-toast.storeops.invalid/"}, "no path"),
        ({"api_base_url": "https://storeops-test-toast.storeops.invalid:8443"}, "no path, port"),
        ({"api_base_url": "https://shop.spicetown.shop"}, "spicetown.shop"),
        ({"api_base_url": "https://spicetown.shop"}, "spicetown.shop"),
        ({"branding__logo_url": "https://cdn.spicetown.shop/logo.png"}, "spicetown.shop"),
        ({"code": "storeops_test_toast"}, "does not match"),
        ({"code": "AB"}, "does not match"),
        ({"status": "retired"}, "status"),
        ({"branding__primary_color": "blue"}, "#RRGGBB"),
        ({"branding__timezone": "Mars/Olympus"}, "IANA"),
        ({"branding__font": "x"}, "unknown keys"),
        ({"surprise": 1}, "unknown keys"),
        ({"name": ...}, "missing 'name'"),
        ({"min_build": {"ios": -1, "android": 0}}, "min_build.ios"),
        ({"min_build": {"ios": True, "android": 0}}, "min_build.ios"),
        ({"message": ""}, "message"),
    ],
)
def test_bad_store_entries_are_refused(changes, expect):
    assert expect in _problems(_one(**changes))


def test_duplicate_code_and_schema_and_every_problem_reported_together():
    data = _example()
    data["stores"].append(dict(data["stores"][0]))
    data["schema"] = 2
    data["stores"][1]["branding"] = dict(data["stores"][1]["branding"], timezone="Nowhere/Land")
    problems = _problems(data)
    assert "duplicate code STOREOPS_TEST_TOAST" in problems
    assert "schema must be 1" in problems
    assert "Nowhere/Land" in problems


def test_extra_api_hosts_allow_a_host_outside_the_platform_domain():
    data = _one(api_base_url="https://ops.example.com")
    assert "not under" in _problems(data)
    stores = parse_stores(data, DirectoryConfig(extra_api_hosts=("ops.example.com",)))
    assert stores["STOREOPS_TEST_TOAST"].api_base_url == "https://ops.example.com"


def test_settings_refuse_spicetown_domains():
    assert is_forbidden_host("spicetown.shop") and is_forbidden_host("A.SpiceTown.Shop.")
    assert not is_forbidden_host("notspicetown.shop")
    with pytest.raises(DirectoryConfigError):
        DirectoryConfig(platform_domain="spicetown.shop")
    with pytest.raises(DirectoryConfigError):
        DirectoryConfig(platform_domain="stores.spicetown.shop")
    with pytest.raises(DirectoryConfigError):
        DirectoryConfig(extra_api_hosts=("api.spicetown.shop",))
    with pytest.raises(DirectoryConfigError):
        DirectoryConfig(app_store_url="https://spicetown.shop/app")


def test_settings_validate_owner_ids_and_store_links(caplog):
    with pytest.raises(DirectoryConfigError):
        DirectoryConfig(apple_team_id="short")
    with pytest.raises(DirectoryConfigError):
        DirectoryConfig(android_cert_sha256=("AB:CD",))
    with pytest.raises(DirectoryConfigError):
        DirectoryConfig(app_bundle_id="not a bundle")
    cfg = DirectoryConfig(apple_team_id="abcde12345", android_cert_sha256=(FP.lower(),))
    assert cfg.apple_team_id == TEAM and cfg.android_cert_sha256 == (FP,)
    # A search page or another app's listing is never linked.
    cfg = DirectoryConfig(
        app_store_url="https://apps.apple.com/us/search?term=Something",
        play_store_url="https://play.google.com/store/apps/details?id=com.example.other",
    )
    assert cfg.app_store_url == ""
    assert cfg.effective_play_store_url == "https://play.google.com/store/apps/details?id=com.kloudc.storeops"
    cfg = DirectoryConfig(app_store_url="https://apps.apple.com/us/app/storeops/id1234567890/")
    assert cfg.app_store_url == "https://apps.apple.com/us/app/storeops/id1234567890"


def test_from_settings_reads_the_store_settings(monkeypatch):
    # MT7 added the DIRECTORY_* fields to Settings (contract 1.1).
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "directory_stores_file", "/tmp/elsewhere.json")
    monkeypatch.setattr(s, "directory_rate_per_minute", 7)
    monkeypatch.setattr(s, "directory_extra_api_hosts", "a.example.com, b.example.com")
    monkeypatch.setattr(s, "directory_client_ip_header", "CF-Connecting-IP")
    cfg = DirectoryConfig.from_settings()
    assert cfg.stores_file == "/tmp/elsewhere.json"
    assert cfg.rate_per_minute == 7 and cfg.rate_per_hour == 100
    assert cfg.extra_api_hosts == ("a.example.com", "b.example.com")
    assert cfg.client_ip_header == "CF-Connecting-IP"
    assert cfg.platform_domain == "storeops.invalid"


def test_normalize_code():
    assert normalize_code("  storeops-test-toast ") == "STOREOPS_TEST_TOAST"
    assert normalize_code("demo") == "DEMO"
    for bad in ("ab", "A" * 25, "_ABC", "BAD!CODE", "", None, "DE MO"):
        assert normalize_code(bad) is None


# --- GET /v1/stores/{code} ---------------------------------------------------


def test_known_code_returns_the_contract_body_and_headers(directory):
    r = _get(directory, "/v1/stores/STOREOPS_TEST_TOAST")
    assert r.status_code == 200
    body = r.json()
    assert list(body) == [
        "schema", "code", "name", "api_base_url", "status", "message", "branding", "min_build", "updated_at"
    ]
    assert body == {
        "schema": 1,
        "code": "STOREOPS_TEST_TOAST",
        "name": "Test Toast Market",
        "api_base_url": "https://storeops-test-toast.storeops.invalid",
        "status": "active",
        "message": None,
        "branding": {
            "short_name": "Toast Test",
            "logo_url": "https://storeops-test-toast.storeops.invalid/branding/logo.png",
            "icon_url": "https://storeops-test-toast.storeops.invalid/branding/icon.png",
            "primary_color": "#1D4ED8",
            "secondary_color": "#93C5FD",
            "timezone": "America/Chicago",
            "pos_name": "Toast",
        },
        "min_build": {"ios": 0, "android": 0},
        "updated_at": "2026-10-18T00:00:00Z",
    }
    assert r.headers["content-type"] == "application/json"
    assert r.headers["cache-control"] == "public, max-age=300, stale-while-revalidate=3600"
    assert r.headers["access-control-allow-origin"] == "*"
    assert r.headers["etag"] == etag_for(r.content)
    assert r.content == compact_json(body)
    assert "set-cookie" not in r.headers


def test_code_is_normalised(directory):
    want = _get(directory, "/v1/stores/STOREOPS_TEST_TOAST").content
    for raw in ("storeops-test-toast", "StoreOps_Test-Toast", "%20storeops_test_toast%20"):
        r = _get(directory, f"/v1/stores/{raw}")
        assert r.status_code == 200, raw
        assert r.content == want


@pytest.mark.parametrize("raw", ["ab", "A" * 25, "_ABC", "BAD!CODE", "DE%20MO"])
def test_invalid_code_is_400(directory, raw):
    r = _get(directory, f"/v1/stores/{raw}")
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_code" and r.json()["message"]
    assert r.headers["cache-control"] == "no-store"


def test_unknown_and_retired_codes_are_the_same_404(directory, stores_file):
    r = _get(directory, "/v1/stores/NO_SUCH_STORE")
    assert r.status_code == 404
    assert r.json() == {"error": "unknown_store", "message": "No store with that code."}
    assert r.headers["cache-control"] == "public, max-age=60"
    assert _get(directory, "/v1/stores/DEMO").status_code == 200
    data = _example()
    data["stores"] = [s for s in data["stores"] if s["code"] != "DEMO"]
    _write_stores(stores_file, data, mtime=1_792_281_700)  # a changed file is re-read
    retired = _get(directory, "/v1/stores/DEMO")
    assert retired.status_code == 404 and retired.content == r.content


def test_maintenance_status_and_message_pass_through(tmp_path, clock):
    data = _one(status="maintenance", message="Back at 6 am.")
    client = TestClient(create_app(_cfg(_write_stores(tmp_path / "s.json", data)), clock=clock))
    body = _get(client, "/v1/stores/STOREOPS_TEST_TOAST").json()
    assert body["status"] == "maintenance" and body["message"] == "Back at 6 am."


def test_etag_and_304(directory):
    r = _get(directory, "/v1/stores/DEMO")
    etag = r.headers["etag"]
    for inm in (etag, f"W/{etag}", f'"nope", {etag}', "*"):
        r2 = _get(directory, "/v1/stores/DEMO", **{"If-None-Match": inm})
        assert r2.status_code == 304, inm
        assert r2.content == b""
        assert r2.headers["etag"] == etag
    assert _get(directory, "/v1/stores/DEMO", **{"If-None-Match": '"other"'}).status_code == 200


def test_rate_limit_per_minute_counts_404s_and_sets_retry_after(stores_file, clock):
    client = TestClient(create_app(_cfg(stores_file, rate_per_minute=3, rate_per_hour=100), clock=clock))
    assert _get(client, "/v1/stores/DEMO").status_code == 200
    assert _get(client, "/v1/stores/NOPE_NOPE").status_code == 404
    assert _get(client, "/v1/stores/x").status_code == 400
    r = _get(client, "/v1/stores/DEMO")
    assert r.status_code == 429
    assert r.headers["retry-after"] == "60" and r.headers["cache-control"] == "no-store"
    assert r.json() == {
        "error": "rate_limited",
        "message": "Too many lookups. Try again in 60 seconds.",
        "retry_after": 60,
    }
    # Another client is not affected.
    assert _get(client, "/v1/stores/DEMO", ip="198.51.100.7").status_code == 200
    clock.t += 30
    assert _get(client, "/v1/stores/DEMO").headers["retry-after"] == "30"  # refused hits are not counted
    clock.t += 30
    assert _get(client, "/v1/stores/DEMO").status_code == 200


def test_rate_limit_per_hour(stores_file, clock):
    client = TestClient(create_app(_cfg(stores_file, rate_per_minute=10, rate_per_hour=5), clock=clock))
    for _ in range(5):
        assert _get(client, "/v1/stores/DEMO").status_code == 200
        clock.t += 61
    r = _get(client, "/v1/stores/DEMO")
    assert r.status_code == 429
    # The first hit (at t=1000) leaves the hour window at t=4600; now is 1305.
    assert int(r.headers["retry-after"]) == 3600 - 5 * 61
    clock.t += 3600 - 5 * 61
    assert _get(client, "/v1/stores/DEMO").status_code == 200


def test_client_ip_comes_from_the_configured_header_else_the_socket(stores_file, clock):
    behind = TestClient(create_app(_cfg(stores_file, rate_per_minute=1), clock=clock))
    assert _get(behind, "/v1/stores/DEMO", ip="203.0.113.9").status_code == 200
    assert _get(behind, "/v1/stores/DEMO", ip="203.0.113.9").status_code == 429
    assert _get(behind, "/v1/stores/DEMO", ip="203.0.113.10").status_code == 200


def test_a_forwarded_for_list_is_keyed_on_the_proxys_entry_not_the_clients(stores_file, clock):
    # With DIRECTORY_CLIENT_IP_HEADER=X-Forwarded-For the client writes the
    # start of the list; only the last entry (added by the proxy in front of
    # the service) can be trusted, so a made-up first entry must not reset
    # the limit.
    cfg = _cfg(stores_file, rate_per_minute=1, client_ip_header="X-Forwarded-For")
    client = TestClient(create_app(cfg, clock=clock))
    xff = "X-Forwarded-For"
    assert client.get("/v1/stores/DEMO", headers={xff: "1.1.1.1, 203.0.113.9"}).status_code == 200
    assert client.get("/v1/stores/DEMO", headers={xff: "2.2.2.2, 203.0.113.9"}).status_code == 429
    assert client.get("/v1/stores/DEMO", headers={xff: "203.0.113.9"}).status_code == 429
    direct = TestClient(create_app(_cfg(stores_file, rate_per_minute=1, client_ip_header=""), clock=clock))
    assert _get(direct, "/v1/stores/DEMO", ip="1.1.1.1").status_code == 200
    # The header is ignored without the setting, so spoofing it does not help.
    assert _get(direct, "/v1/stores/DEMO", ip="2.2.2.2").status_code == 429


def test_broken_file_at_request_time_is_503_until_fixed(directory, stores_file):
    assert _get(directory, "/v1/stores/DEMO").status_code == 200
    stores_file.write_text("{not json")
    r = _get(directory, "/v1/stores/DEMO")
    assert r.status_code == 503
    assert r.json()["error"] == "directory_unavailable"
    assert r.headers["retry-after"] == "30" and r.headers["cache-control"] == "no-store"
    assert _get(directory, "/healthz").status_code == 503
    stores_file.unlink()
    assert _get(directory, "/v1/stores/DEMO").status_code == 503
    _write_stores(stores_file, _example(), mtime=1_792_281_800)
    assert _get(directory, "/v1/stores/DEMO").status_code == 200


def test_service_refuses_to_start_on_a_bad_file(tmp_path):
    bad = _write_stores(tmp_path / "s.json", _one(api_base_url="https://x.spicetown.shop"))
    with pytest.raises(StoresFileError):
        with TestClient(create_app(_cfg(bad))):
            pass
    with pytest.raises(StoresFileError):
        with TestClient(create_app(_cfg(tmp_path / "missing.json"))):
            pass


def test_healthz_and_unknown_paths(directory):
    assert _get(directory, "/healthz").json() == {"ok": True, "stores": 3}
    r = _get(directory, "/v1/nothing")
    assert r.status_code == 404 and r.json()["error"] == "not_found"
    assert _get(directory, "/docs").status_code == 404
    assert _get(directory, "/openapi.json").status_code == 404


# --- association files and the open-in-app page -----------------------------


def test_aasa_and_assetlinks_without_owner_ids(directory):
    r = _get(directory, "/.well-known/apple-app-site-association")
    assert r.status_code == 200 and r.headers["content-type"] == "application/json"
    aasa = r.json()
    assert aasa["applinks"]["details"][0]["appIDs"] == []
    paths = [c["/"] for c in aasa["applinks"]["details"][0]["components"]]
    # Only store links: the app has no /c/ route (MT11 As built), so claiming
    # /c/* would open public checklist links in the app instead of the
    # browser once the owner turns on Associated Domains.
    assert paths == ["/s/*"]
    links = _get(directory, "/.well-known/assetlinks.json").json()
    assert links[0]["target"]["package_name"] == "com.kloudc.storeops"
    assert links[0]["target"]["sha256_cert_fingerprints"] == []
    assert links[0]["relation"] == ["delegate_permission/common.handle_all_urls"]


def test_aasa_and_assetlinks_with_owner_ids(stores_file, clock):
    cfg = _cfg(stores_file, apple_team_id=TEAM, android_cert_sha256=(FP,))
    client = TestClient(create_app(cfg, clock=clock))
    aasa = _get(client, "/.well-known/apple-app-site-association").json()
    assert aasa["applinks"]["details"][0]["appIDs"] == [f"{TEAM}.com.kloudc.storeops"]
    links = _get(client, "/.well-known/assetlinks.json").json()
    assert links[0]["target"]["sha256_cert_fingerprints"] == [FP]


def test_open_page_is_the_same_for_every_code_and_has_a_strict_csp(directory):
    a = _get(directory, "/s/STOREOPS_TEST_TOAST")
    b = _get(directory, "/s/NOT_A_STORE")
    c = _get(directory, "/s/%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E")
    assert a.status_code == b.status_code == c.status_code == 200
    assert a.content == b.content == c.content
    assert a.headers["content-type"].startswith("text/html")
    assert "STOREOPS_TEST_TOAST" not in a.text and "Test Toast Market" not in a.text
    assert "Open this store in the StoreOps app" in a.text
    csp = a.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "'unsafe-inline'" not in csp
    assert "https://play.google.com/store/apps/details?id=com.kloudc.storeops" in a.text
    assert "apps.apple.com" not in a.text  # no App Store link until the owner sets one
    assert _get(directory, "/s").content == a.content


def test_store_server_serves_the_association_files(anon_client, monkeypatch):
    r = anon_client.get("/.well-known/apple-app-site-association")
    assert r.status_code == 200 and r.headers["content-type"] == "application/json"
    assert r.json()["applinks"]["details"][0]["appIDs"] == []
    assert anon_client.get("/.well-known/assetlinks.json").json()[0]["target"]["sha256_cert_fingerprints"] == []
    from app.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "apple_team_id", TEAM.lower())
    monkeypatch.setattr(s, "android_cert_sha256", f"{FP}, not-a-fingerprint")
    r = anon_client.get("/.well-known/apple-app-site-association")
    assert r.json()["applinks"]["details"][0]["appIDs"] == [f"{TEAM}.com.kloudc.storeops"]
    assert r.content == build_static.aasa_bytes(TEAM, "com.kloudc.storeops")
    prints = anon_client.get("/.well-known/assetlinks.json").json()[0]["target"]["sha256_cert_fingerprints"]
    assert prints == [FP]
    monkeypatch.setattr(s, "apple_team_id", "bad")
    assert anon_client.get("/.well-known/apple-app-site-association").json()["applinks"]["details"][0]["appIDs"] == []


# --- static build -------------------------------------------------------------


def test_static_build_matches_the_service_byte_for_byte(directory, stores_file, tmp_path):
    out = tmp_path / "dist"
    (out / "v1" / "stores").mkdir(parents=True)
    (out / "v1" / "stores" / "RETIRED_STORE.json").write_text("{}")
    assert build_static.main(["--stores", str(stores_file), "--out", str(out)]) == 0
    for code in FAKE_CODES:
        assert (out / "v1" / "stores" / f"{code}.json").read_bytes() == _get(directory, f"/v1/stores/{code}").content
    assert not (out / "v1" / "stores" / "RETIRED_STORE.json").exists()
    assert (out / ".well-known" / "apple-app-site-association").read_bytes() == _get(
        directory, "/.well-known/apple-app-site-association"
    ).content
    assert (out / ".well-known" / "assetlinks.json").read_bytes() == _get(
        directory, "/.well-known/assetlinks.json"
    ).content
    page = _get(directory, "/s/DEMO")
    assert (out / "s" / "index.html").read_bytes() == page.content
    headers = (out / "_headers").read_text()
    assert "/v1/stores/*\n  Content-Type: application/json" in headers
    assert "/.well-known/*\n  Content-Type: application/json" in headers
    assert page.headers["content-security-policy"] in headers
    assert "/s/* /s/index.html 200" in (out / "_redirects").read_text()
    # Without a top-level 404 page Cloudflare Pages answers unknown codes 200.
    assert (out / "404.html").exists()


def test_static_build_refuses_a_bad_file(tmp_path, capsys):
    bad = _write_stores(tmp_path / "s.json", _one(code="lower"))
    assert build_static.main(["--stores", str(bad), "--out", str(tmp_path / "o")]) == 2
    assert "does not match" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


def test_refresh_branding_uses_a_fake_fetch_only(stores_file, tmp_path):
    def fetch(url):
        if "storeops-test-clover" in url:
            raise OSError("down")
        if "demo." in url:
            return {"store_code": "SOMEONE_ELSE", "primary_color": "#000000"}
        return {
            "store_code": "STOREOPS_TEST_TOAST",
            "short_name": "Toast New",
            "logo_url": "https://storeops-test-toast.storeops.invalid/branding/logo2.png",
            "icon_url": "https://storeops-test-toast.storeops.invalid/branding/icon.png",
            "primary_color": "#111111",
            "secondary_color": "#222222",
            "timezone": "America/Denver",
            "pos_name": "Toast",
            "address": "not copied",
        }

    stores, notes = build_static.load_for_build(stores_file, DirectoryConfig(), fetch)
    toast = dict(stores.stores["STOREOPS_TEST_TOAST"].branding)
    assert toast["short_name"] == "Toast New" and toast["timezone"] == "America/Denver"
    assert "address" not in toast
    assert dict(stores.stores["DEMO"].branding)["primary_color"] == "#0F766E"
    assert dict(stores.stores["STOREOPS_TEST_CLOVER"].branding)["primary_color"] == "#15803D"
    assert any("SOMEONE_ELSE" in n for n in notes) and any("not refreshed" in n for n in notes)

    def bad_fetch(url):
        return {"store_code": url.split("//")[1].split(".")[0].upper().replace("-", "_"), "timezone": "Bad/Zone"}

    with pytest.raises(StoresFileError):
        build_static.load_for_build(stores_file, DirectoryConfig(), bad_fetch)


# --- the Worker (Node) gives the same answers as the service --------------------

NODE = shutil.which("node")


def _node_major() -> int:
    try:
        out = subprocess.run([NODE, "--version"], capture_output=True, text=True, timeout=30).stdout
        return int(out.strip().lstrip("v").split(".")[0])
    except Exception:
        return 0


needs_node = pytest.mark.skipif(NODE is None or _node_major() < 18, reason="Node 18+ not installed")

_PARITY_HEADERS = [
    "content-type",
    "cache-control",
    "etag",
    "retry-after",
    "access-control-allow-origin",
    "content-security-policy",
    "x-content-type-options",
    "referrer-policy",
]


def _parity_steps():
    toast = "/v1/stores/STOREOPS_TEST_TOAST"
    return [
        {"path": toast},
        {"path": "/v1/stores/storeops-test-clover"},
        {"path": "/v1/stores/DEMO"},
        {"path": "/v1/stores/NO_SUCH_STORE"},
        {"path": "/v1/stores/ab"},
        {"path": "/v1/stores/%20demo%20"},
        {"path": toast, "etag_of": toast},
        {"path": toast, "headers": {"If-None-Match": '"nope"'}},
        {"path": toast},  # the 9th lookup: over the limit of 8
        {"advance": 30},
        {"path": "/v1/stores/DEMO"},
        {"path": "/v1/stores/DEMO", "ip": "198.51.100.20"},
        {"advance": 31},
        {"path": "/v1/stores/DEMO"},
        {"path": "/healthz"},
        {"path": "/.well-known/apple-app-site-association"},
        {"path": "/.well-known/assetlinks.json"},
        {"path": "/s/STOREOPS_TEST_TOAST"},
        {"path": "/s/"},
        {"path": "/nothing/here"},
    ]


@needs_node
def test_worker_answers_exactly_like_the_service(stores_file, tmp_path, clock):
    cfg = _cfg(stores_file, rate_per_minute=8, rate_per_hour=100, apple_team_id=TEAM, android_cert_sha256=(FP,))
    client = TestClient(create_app(cfg, clock=clock))
    data_path = tmp_path / "data.json"
    stores, _ = build_static.load_for_build(stores_file, cfg)
    build_static.build(stores, cfg, worker_data_path=data_path)

    start = clock.t
    steps = []
    etags = {}
    for step in _parity_steps():
        if "advance" in step:
            clock.t += step["advance"]
            steps.append({"advance": step["advance"]})
            continue
        headers = {"CF-Connecting-IP": step.get("ip", "203.0.113.1"), **step.get("headers", {})}
        if "etag_of" in step:
            headers["If-None-Match"] = etags[step["etag_of"]]
        r = client.get(step["path"], headers=headers)
        if r.status_code == 200 and "etag" in r.headers:
            etags.setdefault(step["path"], r.headers["etag"])
        steps.append(
            {
                "path": step["path"],
                "headers": headers,
                "status": r.status_code,
                "body": r.text,
                "expect_headers": {h: r.headers.get(h) for h in _PARITY_HEADERS},
            }
        )
    statuses = [s["status"] for s in steps if "status" in s]
    assert statuses[:9] == [200, 200, 200, 404, 400, 200, 304, 200, 429]
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"start": start, "steps": steps}))
    proc = subprocess.run(
        [NODE, str(WORKER_DIR / "test" / "parity.mjs"), str(data_path), str(cases)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert f"parity ok: {len(statuses)} requests" in proc.stdout


@needs_node
def test_worker_unit_tests_pass():
    proc = subprocess.run(
        [NODE, "--test", str(WORKER_DIR / "test" / "directory.test.mjs")],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(WORKER_DIR),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_open_page_escapes_names_and_links():
    page, csp = open_page('Store<Ops>', 'https://apps.apple.com/app/id1?x="y"', "")
    text = page.decode()
    assert "Store&lt;Ops&gt;" in text and "Store<Ops>" not in text
    assert "&quot;y&quot;" in text
    assert "Google Play" not in text


_ODD_CODE_PATHS = [
    "/v1/stores/%1Cdemo",  # Python's str.strip() drops \x1c-\x1f; JS trim() doesn't
    "/v1/stores/%C2%85demo",  # U+0085 (NEL): Python whitespace, not JS
    "/v1/stores/%EF%BB%BFdemo",  # U+FEFF (BOM): JS whitespace, not Python
    "/v1/stores/%E2%80%A8demo",  # U+2028: whitespace to both
    "/v1/stores/%C3%9Femo",  # "ß" upper-cases to "SS" in both (unknown code)
    "/v1/stores/DE%2FMO",  # an encoded slash: not a lookup path
    "/s/DE%2FMO",
    "/v1/stores/%E0%A4",  # broken UTF-8 escape
    "/v1/stores/%FFDEMO",
    "/v1/stores/de-mo%09",  # "DE_MO" (unknown)
]


@needs_node
def test_worker_and_service_agree_on_odd_codes(stores_file, tmp_path, clock):
    cfg = _cfg(stores_file, rate_per_minute=1000, rate_per_hour=10000)
    client = TestClient(create_app(cfg, clock=clock))
    data_path = tmp_path / "data.json"
    stores, _ = build_static.load_for_build(stores_file, cfg)
    build_static.build(stores, cfg, worker_data_path=data_path)
    steps = []
    for path in _ODD_CODE_PATHS:
        headers = {"CF-Connecting-IP": "203.0.113.1"}
        r = client.get(path, headers=headers)
        steps.append(
            {
                "path": path,
                "headers": headers,
                "status": r.status_code,
                "body": r.text,
                "expect_headers": {h: r.headers.get(h) for h in _PARITY_HEADERS},
            }
        )
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps({"start": clock.t, "steps": steps}))
    proc = subprocess.run(
        [NODE, str(WORKER_DIR / "test" / "parity.mjs"), str(data_path), str(cases)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    # Only ASCII whitespace is trimmed; nothing else turns into a valid code.
    assert [s["status"] for s in steps] == [400, 400, 400, 400, 404, 404, 404, 400, 400, 404]
    assert json.loads(steps[5]["body"])["error"] == "not_found"
