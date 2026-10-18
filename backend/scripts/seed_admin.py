"""One-time seed of the initial admin account. Safe to re-run - does nothing
if any user already exists, so it won't reset a password that's since been
changed. Run with the backend venv active:

    SEED_ADMIN_USERNAME=admin python scripts/seed_admin.py

The password is never stored in the repository: it is read from the
SEED_ADMIN_PASSWORD environment variable, or asked for interactively.
"""

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import hash_password
from app.db import SessionLocal
from app.models import User

MIN_PASSWORD_LENGTH = 10


def _admin_credentials() -> tuple:
    username = (os.environ.get("SEED_ADMIN_USERNAME") or "admin").strip()
    password = os.environ.get("SEED_ADMIN_PASSWORD") or ""
    if not password and sys.stdin.isatty():
        password = getpass.getpass(f"Password for new admin '{username}': ")
    if len(password) < MIN_PASSWORD_LENGTH:
        sys.exit(
            f"Set SEED_ADMIN_PASSWORD (at least {MIN_PASSWORD_LENGTH} characters) "
            "or run interactively to type one."
        )
    return username, password


def main() -> None:
    db = SessionLocal()
    try:
        if db.query(User).count() > 0:
            print("Users already exist - not touching anything. Exiting.")
            return

        username, password = _admin_credentials()
        user = User(
            username=username,
            password_hash=hash_password(password),
            is_admin=True,
        )
        db.add(user)
        db.commit()
        print(f"Created admin user '{username}'.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
