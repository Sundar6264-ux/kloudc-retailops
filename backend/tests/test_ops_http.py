"""Request ids, access log, error answers and security headers
(app/ops/http.py, app/ops/logs.py)."""

import json
import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import process_settings
from app.ops import http as ops_http
from app.ops import logs


def test_every_answer_has_a_request_id_and_echoes_a_valid_one(anon_client):
    r = anon_client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    rid = r.headers["x-request-id"]
    assert len(rid) == 32
    r = anon_client.get("/api/health", headers={"X-Request-ID": "probe-12345678"})
    assert r.headers["x-request-id"] == "probe-12345678"
    r = anon_client.get("/api/health", headers={"X-Request-ID": "bad id\nwith newline"})
    assert r.headers["x-request-id"] != "bad id\nwith newline" and len(r.headers["x-request-id"]) == 32


def test_security_headers_on_api_answers(anon_client):
    r = anon_client.get("/api/health")
    h = r.headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "strict-origin-when-cross-origin"
    assert h["x-frame-options"] == "SAMEORIGIN"
    assert "camera=(self)" in h["permissions-policy"]
    assert "content-security-policy" not in h, "CSP only on HTML documents"
    # Production (the default APP_ENV) + secure cookies: HSTS.
    assert h["strict-transport-security"] == ops_http.HSTS_VALUE


def test_no_hsts_outside_production_or_without_secure_cookies(anon_client, monkeypatch):
    monkeypatch.setattr(process_settings(), "app_env", "development")
    assert "strict-transport-security" not in anon_client.get("/api/health").headers


def test_csp_on_html_allows_self_and_the_build_s_inline_scripts(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<html><head><script>document.documentElement.dataset.theme='dark'</script>"
        '<script type="module" src="/assets/app.js"></script></head></html>'
    )
    monkeypatch.setattr(process_settings(), "frontend_dist_dir", str(dist))
    headers = dict(ops_http.security_headers("/", "text/html; charset=utf-8"))
    csp = headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'self'" in csp
    assert "script-src 'self' 'sha256-" in csp, csp
    assert "unsafe-inline" not in csp.split("script-src", 1)[1].split(";", 1)[0]
    assert "fonts.googleapis" not in csp
    monkeypatch.setattr(process_settings(), "security_headers", "report-only")
    headers = dict(ops_http.security_headers("/", "text/html"))
    assert "Content-Security-Policy-Report-Only" in headers and "Content-Security-Policy" not in headers
    monkeypatch.setattr(process_settings(), "security_headers", "off")
    assert ops_http.security_headers("/", "text/html") == []


def test_a_route_s_own_header_wins():
    app = FastAPI()
    app.add_middleware(ops_http.RequestContextMiddleware)

    @app.get("/page")
    def page():
        from fastapi.responses import HTMLResponse

        return HTMLResponse("<p>x</p>", headers={"Content-Security-Policy": "default-src 'none'",
                                                 "X-Frame-Options": "DENY"})

    r = TestClient(app).get("/page")
    assert r.headers["content-security-policy"] == "default-src 'none'"
    assert r.headers["x-frame-options"] == "DENY"


def test_an_unhandled_error_is_logged_with_its_request_id_and_answered_as_json(caplog):
    app = FastAPI()
    app.add_middleware(ops_http.RequestContextMiddleware)
    app.add_exception_handler(Exception, ops_http.server_error_handler)

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internals")

    client = TestClient(app, raise_server_exceptions=False)
    with caplog.at_level(logging.INFO):
        r = client.get("/boom", headers={"X-Request-ID": "trace-0001-abcd"})
    assert r.status_code == 500
    body = r.json()
    assert body == {"detail": "Something went wrong on our side. Try again in a minute.", "error": "server_error",
                    "request_id": "trace-0001-abcd"}
    assert "secret internals" not in r.text
    errors = [rec for rec in caplog.records if rec.name == "retailops.error"]
    assert errors and errors[0].request_id == "trace-0001-abcd" and errors[0].exc_info
    access = [rec for rec in caplog.records if rec.name == "retailops.access"]
    assert access and access[-1].fields["status"] == 500 and access[-1].fields["path"] == "/boom"


def test_access_log_never_has_the_query_string(anon_client, caplog):
    with caplog.at_level(logging.INFO, logger="retailops.access"):
        anon_client.get("/api/health?token=sekrit")
    lines = [r.getMessage() for r in caplog.records if r.name == "retailops.access"]
    assert lines and all("sekrit" not in line for line in lines)


def test_access_log_client_is_the_visitor_behind_the_tunnel_only(monkeypatch):
    """Pre-production Verify: behind Cloudflare every request comes from
    127.0.0.1 (cloudflared), so the access log names the visitor from
    CF-Connecting-IP - the same rule as the login lockout (trusted peer
    only); anyone else's header is ignored."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1")

    def scope(peer, header=None):
        headers = [(b"cf-connecting-ip", header.encode())] if header else []
        return {"type": "http", "client": (peer, 5000), "headers": headers}

    assert ops_http._client(scope("127.0.0.1", "203.0.113.7")) == "203.0.113.7"
    assert ops_http._client(scope("198.51.100.4", "203.0.113.7")) == "198.51.100.4"
    assert ops_http._client(scope("127.0.0.1", "not-an-ip")) == "127.0.0.1"
    assert ops_http._client(scope("127.0.0.1")) == "127.0.0.1"
    assert ops_http._client({"type": "http", "headers": []}) == "-"


def test_json_log_lines_carry_request_id_and_store(tmp_path):
    logs._install_record_factory()
    token = logs.request_id_var.set("rid-xyz-0001")
    try:
        record = logging.getLogger("x.y").makeRecord("x.y", logging.ERROR, __file__, 1, "hello %s", ("you",), None)
    finally:
        logs.request_id_var.reset(token)
    out = json.loads(logs.JsonFormatter().format(record))
    assert out["msg"] == "hello you" and out["request_id"] == "rid-xyz-0001" and out["level"] == "ERROR"
    assert "store" in out and out["ts"].endswith("+00:00")


def test_log_dir_gets_app_and_error_logs(tmp_path):
    root = logging.getLogger()
    s = process_settings().model_copy(update={"log_dir": str(tmp_path / "logs"), "log_format": "json"})
    try:
        logs.configure_logging(s, force=True)
        logging.getLogger("ops.test").error("it broke")
        logging.getLogger("ops.test").info("just info")
        for h in root.handlers:
            h.flush()
        app_log = (tmp_path / "logs" / logs.APP_LOG).read_text()
        err_log = (tmp_path / "logs" / logs.ERROR_LOG).read_text()
        assert "it broke" in app_log and "just info" in app_log
        assert "it broke" in err_log and "just info" not in err_log
        json.loads(err_log.splitlines()[0])
    finally:
        logs.configure_logging(process_settings(), force=True)
    assert [h for h in root.handlers if getattr(h, "_retailops", False) and isinstance(h, logging.FileHandler)] == []
