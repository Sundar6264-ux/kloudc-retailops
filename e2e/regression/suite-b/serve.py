"""Suite B throwaway server: a PROD COPY migrated to head, every send blocked or mocked.

Refuses to start unless the database is the copy under SUITE_B_WORK, Whapi points at
127.0.0.1 with the fake token, and Toast / Analytics / Firebase / Anthropic are blank.
smtplib is replaced by a fake that writes to <work>/outbox."""
import json
import os
import pathlib
import smtplib
import sys

W = pathlib.Path(os.environ["SUITE_B_WORK"]).resolve()
PORT = int(os.environ["SUITE_B_PORT"])
assert os.environ["DATABASE_URL"] == f"sqlite:///{W}/prodcopy.db", os.environ["DATABASE_URL"]
assert not (os.environ.get("ORIGIN_LIVE_DIR") and os.environ["ORIGIN_LIVE_DIR"] in os.environ["DATABASE_URL"]), "never the live database"


class FakeSMTP:
    def __init__(self, *a, **kw):
        pass

    def login(self, user, password):
        pass

    def sendmail(self, sender, to, message):
        n = len(list((W / "outbox").glob("*.eml"))) + 1
        (W / "outbox" / f"{n:03d}.eml").write_text(message if isinstance(message, str) else message.decode())
        (W / "outbox" / f"{n:03d}.json").write_text(json.dumps({"from": sender, "to": to}))
        return {}

    def send_message(self, msg, from_addr=None, to_addrs=None):
        return self.sendmail(from_addr, to_addrs, msg.as_string())

    def ehlo(self, *a):
        pass

    def starttls(self, *a, **k):
        pass

    def quit(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


smtplib.SMTP_SSL = FakeSMTP
smtplib.SMTP = FakeSMTP
sys.path.insert(0, os.path.join(os.environ["SUITE_B_REPO"], "backend"))
from app.config import get_settings  # noqa: E402

s = get_settings()
assert s.database_url.endswith(f"{W}/prodcopy.db")
assert s.whapi_base_url.startswith("http://127.0.0.1:") and s.whapi_api_token == "mock-token-not-real"
assert not s.scheduler_enabled and not s.notifications_enabled
assert not s.firebase_credentials_file and not s.anthropic_api_key
assert not s.toast_client_id and not s.toast_analytics_client_id
assert str(pathlib.Path(s.flyers_dir).resolve()).startswith(str(W))
from app.main import app  # noqa: E402
import uvicorn  # noqa: E402

uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
