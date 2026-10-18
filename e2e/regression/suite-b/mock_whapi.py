"""Local stand-in for Whapi Cloud (never the real one) for Suite B.

Records every request to <work>/whapi_log.jsonl. Chat ids listed one per line in
<work>/whapi_fail.txt get a 400 on /messages/* (a definite failure, so Retry can be
tested by emptying the file). GET /groups lists a few fake groups."""
import json
import os
import pathlib
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

W = pathlib.Path(os.environ.get("SUITE_B_WORK", "/Users/sundar/.claude/jobs/e26a1611/tmp/suiteB"))
PORT = int(os.environ.get("SUITE_B_WHAPI_PORT", "8838"))
LOG = W / "whapi_log.jsonl"
FAIL = W / "whapi_fail.txt"
GROUPS = [
    {"id": "120363000000000101@g.us", "name": "Suite B Customers East"},
    {"id": "120363000000000102@g.us", "name": "Suite B Customers Manchester"},
    {"id": "120363000000000103@g.us", "name": "Suite B Kitchen staff"},
]
lock = threading.Lock()
counter = {"n": 0}


def nxt(prefix):
    with lock:
        counter["n"] += 1
        return f"{prefix}-{counter['n']}"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _record(self, body_info):
        with lock, open(LOG, "a") as f:
            f.write(json.dumps({"method": self.command, "path": self.path, "auth": self.headers.get("Authorization", ""),
                                **body_info}) + "\n")

    def do_GET(self):
        self._record({})
        if self.path.startswith("/groups"):
            return self._send(200, {"groups": GROUPS, "count": len(GROUPS), "total": len(GROUPS), "offset": 0})
        if self.path.startswith("/messages/list/"):
            return self._send(200, {"messages": [], "count": 0, "total": 0})
        return self._send(404, {"error": "mock: unknown"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        if self.path.startswith("/media"):
            self._record({"bytes": len(raw), "content_type": self.headers.get("Content-Type")})
            return self._send(200, {"media": [{"id": nxt("mock-media")}]})
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {}
        media = body.get("media")
        info = {"to": body.get("to"), "caption": body.get("caption"), "filename": body.get("filename"),
                "media": (media[:40] + "...") if isinstance(media, str) and len(media) > 40 else media,
                "body": body.get("body")}
        self._record(info)
        fails = set(FAIL.read_text().split()) if FAIL.exists() else set()
        if body.get("to") in fails:
            return self._send(400, {"error": {"code": 400, "message": "mock: refused"}})
        if self.path.startswith("/messages/"):
            return self._send(200, {"sent": True, "message": {"id": nxt("mock-msg"), "chat_id": body.get("to")}})
        return self._send(404, {"error": "mock: unknown"})


if __name__ == "__main__":
    W.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"mock whapi on {PORT}", file=sys.stderr)
    srv.serve_forever()
