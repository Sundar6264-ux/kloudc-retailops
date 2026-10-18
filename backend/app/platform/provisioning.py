"""Store provisioning (contract section 10; owner C).

Approving a sign-up creates the ``stores`` row (``pending``) and starts
``provision_store(code)`` in a background thread; the API answers 202.
Eight steps, each idempotent and recorded in ``stores.provisioning_step``
(the last one completed). A failure sets ``status='failed'`` with a plain
reason and audits ``store.provision_failed``; **Retry** resumes after the
last completed step.

1. reserve          status provisioning; db_name; refuse a code/db taken by another store
2. create_database  CREATE DATABASE ... TEMPLATE template0 LC_COLLATE 'C' LC_CTYPE 'C'
                    (exists: only an empty database or one stamped with this code)
3. migrate          app.migrate.upgrade_store_db (track A) to head
4. store_init       app.store.init_store with the pack; identity stamp -> stores.instance_id
5. owner_admin      the owner's admin login (bcrypt hash from the sign-up, or a
                    one-time setup link when there is no password)
6. settings         initial store_settings (name, time zone, POS, address, phone, email)
7. files            <UPLOADS_ROOT>/<CODE>/{team,invoices,flyers,.keys}
8. activate         status active; scheduler told; audit store.provisioned

Never automatic: dropping a database (``python -m app.platform purge-store``).
"""

import datetime as dt
import json
import logging
import pathlib
import re
import secrets
import threading
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from app.platform import bridge
from app.platform.audit import audit
from app.platform.codes import db_name_for, is_reserved, normalize_code
from app.platform.config import platform_public_url, pget
from app.platform.db import make_engine, platform_session
from app.platform.errors import PlatformError
from app.platform.models import OwnerSetupToken, SignupRequest, Store, StoreSetting, utcnow

logger = logging.getLogger(__name__)

STEPS = ("reserve", "create_database", "migrate", "store_init", "owner_admin", "settings", "files", "activate")
PACKS_DIR = pathlib.Path(__file__).resolve().parents[2] / "packs"
PACK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
DEFAULT_PACK = "generic"
UNUSABLE_HASH = "!"  # never matches bcrypt: login refused until the setup link is used
UPLOAD_SUBDIRS = ("team", "invoices", "flyers", ".keys")


class ProvisionError(RuntimeError):
    """A step failed; the message is shown to masters (no secrets)."""


# --- packs -------------------------------------------------------------------------


def _pack_dirs() -> List[pathlib.Path]:
    dirs = [PACKS_DIR]
    private = str(pget("private_packs_dir") or "").strip()
    if private:
        dirs.insert(0, pathlib.Path(private).expanduser())
    return dirs


def list_packs() -> List[Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for d in reversed(_pack_dirs()):
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.json")):
            if p.name.endswith(".schema.json") or not PACK_ID_RE.match(p.stem):
                continue
            label = p.stem
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                label = str(data.get("label") or p.stem)
            except (OSError, ValueError):
                continue
            out[p.stem] = {"pack_id": p.stem, "label": label, "private": d != PACKS_DIR}
    return [out[k] for k in sorted(out)]


def pack_path(pack_id: str) -> pathlib.Path:
    if not PACK_ID_RE.match(pack_id or ""):
        raise PlatformError(400, "invalid", "Unknown pack.", field="pack_id")
    for d in _pack_dirs():
        p = d / f"{pack_id}.json"
        if p.is_file():
            return p
    raise PlatformError(400, "invalid", f"Unknown pack {pack_id!r}.", field="pack_id")


# --- links -----------------------------------------------------------------------------


def login_url(code: str) -> str:
    return f"{platform_public_url()}/#/login?store={code}"


def _hash(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_setup_link(db: Session, store: Store, username: str, *, actor: str) -> str:
    """A one-time link for ``username`` to set a password (shown once)."""
    token = secrets.token_urlsafe(32)
    hours = int(pget("owner_setup_link_hours") or 72)
    db.add(
        OwnerSetupToken(
            token_hash=_hash(token),
            store_id=store.id,
            username=username,
            created_by=actor,
            created_at=utcnow(),
            expires_at=utcnow() + dt.timedelta(hours=hours),
        )
    )
    audit("store.setup_link_issued", actor_type="platform_user" if actor.startswith("platform:") else "system",
          actor_name=actor, store_code=store.code, target=username, details={"hours": hours}, db=db)
    return f"{platform_public_url()}/#/setup?store={store.code}&token={token}"


def redeem_setup_link(code: str, token: str, password: str, client_ip: Optional[str] = None) -> str:
    """Set the owner's password from a setup link. Returns the username."""
    from app.auth import delete_all_sessions_for_user, hash_password
    from app.models import User

    if not isinstance(password, str) or len(password) < 10 or len(password) > 256:
        raise PlatformError(400, "invalid", "The password needs at least 10 characters.", field="password")
    norm = normalize_code(code)
    with platform_session() as pdb:
        store = pdb.execute(select(Store).where(Store.code == (norm or ""))).scalar_one_or_none()
        row = pdb.get(OwnerSetupToken, _hash(token or "")) if isinstance(token, str) else None
        if (
            store is None
            or row is None
            or row.store_id != store.id
            or row.used_at is not None
            or row.expires_at < utcnow()
            or store.status not in ("active", "suspended")
        ):
            raise PlatformError(404, "not_found", "This link is not valid any more. Ask for a new one.")
        username = row.username
        with _store_db(store) as sdb:
            user = sdb.execute(select(User).where(func.lower(User.username) == username.lower())).scalar_one_or_none()
            if user is None:
                raise PlatformError(404, "not_found", "This link is not valid any more. Ask for a new one.")
            user.password_hash = hash_password(password)
            sdb.commit()
            delete_all_sessions_for_user(sdb, user.id)
        row.used_at = utcnow()
        audit("store.owner_password_set", actor_type="store_user", actor_name=f"store:{store.code}:{username}",
              store_code=store.code, target=username, client_ip=client_ip, db=pdb)
        pdb.commit()
    return username


# --- store database helpers ---------------------------------------------------------------


class _StoreDb:
    def __init__(self, store: Store):
        self.url = bridge.store_url(store.db_name, store.db_host_ref)
        self.engine = None
        self.session: Optional[Session] = None

    def __enter__(self) -> Session:
        self.engine = make_engine(self.url, pool_size=1, max_overflow=1)
        self.session = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)()
        return self.session

    def __exit__(self, *exc) -> None:
        try:
            if self.session is not None:
                self.session.close()
        finally:
            if self.engine is not None:
                self.engine.dispose()


def _store_db(store: Store) -> _StoreDb:
    return _StoreDb(store)


def store_uploads_root(code: str) -> pathlib.Path:
    t = bridge.tenancy()
    if t is not None and hasattr(t, "store_root"):
        try:
            return pathlib.Path(t.store_root(code))
        except Exception:  # noqa: BLE001 - B may refuse an inactive store; same rule below
            pass
    base = pathlib.Path(str(pget("uploads_root") or "uploads")).expanduser().resolve()
    return base / code


def default_migrator(url: str) -> str:
    try:
        from app.migrate import upgrade_store_db
    except ImportError:
        raise ProvisionError("app.migrate (track A) is not installed: cannot migrate a PostgreSQL store database") from None
    return upgrade_store_db(url)


# Tests and the integration stage may swap these.
MIGRATOR: Callable[[str], str] = default_migrator
INIT_STORE: Optional[Callable[..., dict]] = None  # default app.store.init_store


def _quote_ident(name: str) -> str:
    if not re.match(r"^[a-z_][a-z0-9_]{0,62}$", name):
        raise ProvisionError(f"{name!r} is not a safe database name")
    return '"' + name + '"'


def _database_exists(admin_engine, name: str) -> bool:
    with admin_engine.connect() as conn:
        return conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).first() is not None


def _database_state(url: str) -> Dict[str, Any]:
    """{"tables": n, "stamp": code or None} of an existing store database."""
    eng = make_engine(url, pool_size=1, max_overflow=0)
    try:
        with eng.connect() as conn:
            n = conn.execute(
                text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
            ).scalar() or 0
            stamp = None
            if conn.execute(
                text("SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name='store_identity'")
            ).first():
                stamp = conn.execute(text("SELECT store_code FROM store_identity WHERE id = 1")).scalar()
            return {"tables": int(n), "stamp": stamp}
    finally:
        eng.dispose()


# --- the steps -----------------------------------------------------------------------------


def _step_reserve(pdb: Session, store: Store) -> None:
    expected = db_name_for(store.code)
    if store.db_name != expected and store.db_host_ref is None:
        raise ProvisionError(f"database name {store.db_name} does not match the code {store.code}")
    clash = pdb.execute(
        select(Store.code).where(Store.id != store.id, (Store.code == store.code) | (Store.db_name == store.db_name))
    ).first()
    if clash is not None:
        raise ProvisionError(f"the code or database name is already used by store {clash[0]}")


def _step_create_database(pdb: Session, store: Store) -> None:
    url = bridge.store_url(store.db_name, store.db_host_ref)
    if not url.startswith("postgresql"):
        raise ProvisionError("store databases are PostgreSQL (STORE_DATABASE_URL_TEMPLATE)")
    admin = make_engine(bridge.admin_url(), isolation_level="AUTOCOMMIT", pool_size=1, max_overflow=0)
    try:
        if _database_exists(admin, store.db_name):
            state = _database_state(url)
            if state["stamp"] not in (None, store.code):
                raise ProvisionError(f"database {store.db_name} belongs to store {state['stamp']}; refusing to adopt it")
            if state["stamp"] is None and state["tables"] and not _resuming(store):
                raise ProvisionError(f"database {store.db_name} already exists and is not empty; refusing to adopt it")
            return
        with admin.connect() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE {_quote_ident(store.db_name)} TEMPLATE template0 "
                    "ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C'"
                )
            )
    finally:
        admin.dispose()


def _resuming(store: Store) -> bool:
    """A retry after this store's own create_database step: its own tables."""
    done = store.provisioning_step
    return done is not None and STEPS.index(done) >= STEPS.index("create_database")


def _step_migrate(pdb: Session, store: Store) -> None:
    url = bridge.store_url(store.db_name, store.db_host_ref)
    store.schema_revision = (MIGRATOR(url) or "")[:32] or None


def _step_store_init(pdb: Session, store: Store) -> None:
    from app.store.__main__ import init_store

    fn = INIT_STORE or init_store
    path = pack_path(store.pack_id)
    root = store_uploads_root(store.code)
    with _store_db(store) as sdb, bridge.store_context(store.code, allow_inactive=True):
        summary = fn(sdb, store.code, str(path), uploads_root=root, reload_caches=bridge.tenancy() is not None)
    store.instance_id = (summary.get("instance_id") or "")[:32] or None


def _step_owner_admin(pdb: Session, store: Store) -> Optional[str]:
    """Returns a setup link when the owner has no password yet."""
    from app.models import User

    username = store.owner_username
    if not username:
        raise ProvisionError("no owner username on this store")
    pw_hash = None
    if store.signup_request_id:
        req = pdb.get(SignupRequest, store.signup_request_id)
        if req is not None and req.owner_password_hash and req.owner_password_hash != UNUSABLE_HASH:
            pw_hash = req.owner_password_hash
    with _store_db(store) as sdb:
        user = sdb.execute(select(User).where(func.lower(User.username) == username.lower())).scalar_one_or_none()
        if user is None:
            user = User(username=username, password_hash=pw_hash or UNUSABLE_HASH, is_admin=True, allowed_features="[]")
            sdb.add(user)
        else:
            user.is_admin = True
            if pw_hash and user.password_hash in (UNUSABLE_HASH, "", None):
                user.password_hash = pw_hash
        sdb.commit()
        needs_link = user.password_hash == UNUSABLE_HASH
    if needs_link:
        return issue_setup_link(pdb, store, username, actor="system:provisioning")
    return None


def _initial_settings(pdb: Session, store: Store) -> Dict[str, Any]:
    values: Dict[str, Any] = {"store_name": store.name, "store_timezone": store.timezone, "pos_provider": store.pos_provider}
    if store.signup_request_id:
        req = pdb.get(SignupRequest, store.signup_request_id)
        if req is not None:
            if req.address:
                values["store_address"] = req.address
            if req.contact_phone:
                values["store_phone"] = req.contact_phone
            if req.contact_email:
                values["store_email"] = req.contact_email
    return values


def _step_settings(pdb: Session, store: Store) -> None:
    have = set(pdb.execute(select(StoreSetting.key).where(StoreSetting.store_id == store.id)).scalars())
    for key, value in _initial_settings(pdb, store).items():
        if key in have:
            continue
        pdb.add(StoreSetting(store_id=store.id, key=key, value=json.dumps(value), updated_at=utcnow(), updated_by="system"))


def _step_files(pdb: Session, store: Store) -> None:
    root = store_uploads_root(store.code)
    root.mkdir(parents=True, exist_ok=True)
    for sub in UPLOAD_SUBDIRS:
        (root / sub).mkdir(exist_ok=True)
    try:
        (root / ".keys").chmod(0o700)
    except OSError:
        pass


def _step_activate(pdb: Session, store: Store) -> None:
    store.status = "active"
    store.status_reason = None
    if store.signup_request_id:
        req = pdb.get(SignupRequest, store.signup_request_id)
        if req is not None:
            # The hash has done its job (it is in the store's users table now).
            req.owner_password_hash = UNUSABLE_HASH


STEP_FUNCS = {
    "reserve": _step_reserve,
    "create_database": _step_create_database,
    "migrate": _step_migrate,
    "store_init": _step_store_init,
    "owner_admin": _step_owner_admin,
    "settings": _step_settings,
    "files": _step_files,
    "activate": _step_activate,
}


# --- running -------------------------------------------------------------------------------------

_running: Dict[str, threading.Thread] = {}
_running_lock = threading.Lock()
_results: Dict[str, Dict[str, Any]] = {}


def _claim(code: str) -> bool:
    """pending/failed -> provisioning (one runner per store, across processes)."""
    with platform_session() as pdb:
        n = pdb.execute(
            update(Store)
            .where(Store.code == code, Store.status.in_(("pending", "failed")))
            .values(status="provisioning", status_reason=None, updated_at=utcnow())
        ).rowcount
        pdb.commit()
        return bool(n)


def provision_store(code: str, *, actor: str = "system") -> Dict[str, Any]:
    """Run (or resume) the steps for one store, in this thread."""
    if not _claim(code):
        with platform_session() as pdb:
            store = pdb.execute(select(Store).where(Store.code == code)).scalar_one_or_none()
            if store is None:
                raise PlatformError(404, "unknown_store")
            raise PlatformError(409, "conflict", f"Store {code} is {store.status}; nothing to provision.")
    result: Dict[str, Any] = {"store_code": code, "setup_url": None}
    with platform_session() as pdb:
        store = pdb.execute(select(Store).where(Store.code == code)).scalar_one()
        start = 0 if store.provisioning_step is None else STEPS.index(store.provisioning_step) + 1
        audit("store.provisioning_started", actor_type="system", actor_name=actor, store_code=code,
              details={"from_step": STEPS[start] if start < len(STEPS) else "done"}, db=pdb)
        pdb.commit()
        for step in STEPS[start:]:
            try:
                out = STEP_FUNCS[step](pdb, store)
                if step == "owner_admin" and out:
                    result["setup_url"] = out
                store.provisioning_step = step
                store.updated_at = utcnow()
                pdb.commit()
            except Exception as exc:  # noqa: BLE001 - every failure is recorded, then re-raised for the CLI
                pdb.rollback()
                reason = _plain_reason(step, exc)
                logger.exception("provisioning %s failed at %s", code, step)
                store = pdb.execute(select(Store).where(Store.code == code)).scalar_one()
                store.status = "failed"
                store.status_reason = reason
                store.updated_at = utcnow()
                audit("store.provision_failed", actor_type="system", actor_name=actor, store_code=code,
                      details={"step": step, "reason": reason}, db=pdb)
                pdb.commit()
                result.update({"status": "failed", "failed_step": step, "reason": reason})
                _results[code] = result
                return result
        audit("store.provisioned", actor_type="system", actor_name=actor, store_code=code,
              details={"pack_id": store.pack_id, "instance_id": store.instance_id, "schema_revision": store.schema_revision},
              db=pdb)
        pdb.commit()
    bridge.store_changed(code)
    result["status"] = "active"
    result["login_url"] = login_url(code)
    _results[code] = result
    return result


def _plain_reason(step: str, exc: BaseException) -> str:
    msg = str(exc) if isinstance(exc, (ProvisionError, PlatformError)) else type(exc).__name__
    if isinstance(exc, PlatformError):
        msg = exc.message
    # Never a URL with a password in the reason.
    msg = re.sub(r"://[^@/\s]+@", "://***@", msg)
    return f"{step}: {msg}"[:500]


def start_provisioning(code: str, *, actor: str = "system") -> threading.Thread:
    """``provision_store`` in a background thread (no store context)."""
    with _running_lock:
        t = _running.get(code)
        if t is not None and t.is_alive():
            return t

        def _run() -> None:
            try:
                provision_store(code, actor=actor)
            except Exception:  # noqa: BLE001 - recorded on the row already
                logger.exception("provisioning thread for %s", code)

        t = threading.Thread(target=_run, name=f"provision-{code}", daemon=True)
        _running[code] = t
        t.start()
        return t


def wait_for(code: str, timeout: float = 120.0) -> Optional[Dict[str, Any]]:
    """Tests / CLI: join the background run for ``code``."""
    t = _running.get(code)
    if t is not None:
        t.join(timeout)
    return _results.get(code)


def last_result(code: str) -> Optional[Dict[str, Any]]:
    return _results.get(code)


# --- approve / manual create / retry --------------------------------------------------------------


def _check_code_free(pdb: Session, code: str, *, own_request_id: Optional[int] = None) -> None:
    if is_reserved(code):
        raise PlatformError(409, "code_unavailable", "That code is reserved.", reason="reserved")
    if pdb.execute(select(Store.id).where((Store.code == code) | (Store.db_name == db_name_for(code)))).first():
        raise PlatformError(409, "code_unavailable", "That code is already used by another store.", reason="taken")
    q = select(SignupRequest.id).where(SignupRequest.requested_code == code, SignupRequest.status == "pending")
    if own_request_id is not None:
        q = q.where(SignupRequest.id != own_request_id)
    if pdb.execute(q).first():
        raise PlatformError(409, "code_unavailable", "Another pending request asks for that code.", reason="requested")


def approve_signup(
    request_id: int,
    *,
    actor: str,
    overrides: Optional[Dict[str, Any]] = None,
    client_ip: Optional[str] = None,
    run: str = "background",
) -> Dict[str, Any]:
    """Create the store row and start provisioning. ``run``: "background"
    (the API: 202), "inline" (CLI / tests) or "none"."""
    import zoneinfo

    overrides = overrides or {}
    with platform_session() as pdb:
        q = select(SignupRequest).where(SignupRequest.id == int(request_id))
        if pdb.bind.dialect.name == "postgresql":
            q = q.with_for_update()
        req = pdb.execute(q).scalar_one_or_none()
        if req is None:
            raise PlatformError(404, "not_found")
        if req.status != "pending":
            raise PlatformError(409, "conflict", f"This request is already {req.status}.")
        code = normalize_code(overrides.get("code")) if overrides.get("code") else req.requested_code
        if code is None:
            raise PlatformError(400, "invalid_code", field="code")
        _check_code_free(pdb, code, own_request_id=req.id)
        tz = (overrides.get("timezone") or req.timezone or "").strip()
        try:
            zoneinfo.ZoneInfo(tz)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError):
            raise PlatformError(400, "invalid", "Unknown time zone.", field="timezone") from None
        pack_id = (overrides.get("pack_id") or DEFAULT_PACK).strip()
        pack_path(pack_id)  # 400 when unknown
        now = utcnow()
        store = Store(
            code=code,
            name=req.store_name,
            status="pending",
            hosting="shared",
            db_name=db_name_for(code),
            timezone=tz,
            pos_provider=req.pos_provider,
            pack_id=pack_id,
            settings_version=1,
            signup_request_id=req.id,
            owner_username=req.owner_username,
            created_at=now,
            created_by=f"signup:{req.id}",
            approved_by=actor,
            approved_at=now,
            updated_at=now,
        )
        pdb.add(store)
        pdb.flush()
        req.status = "approved"
        req.reviewed_by = actor
        req.reviewed_at = now
        req.review_note = (overrides.get("note") or "").strip()[:1000] or None
        req.store_id = store.id
        audit("signup.approved", actor_type="platform_user", actor_name=actor, store_code=code,
              target=f"signup:{req.id}",
              details={"requested_code": req.requested_code, "pack_id": pack_id, "timezone": tz}, client_ip=client_ip, db=pdb)
        pdb.commit()
    out = {"store_code": code, "status": "provisioning", "login_url": login_url(code)}
    if run == "background":
        start_provisioning(code, actor=actor)
    elif run == "inline":
        out.update(provision_store(code, actor=actor))
    return out


def create_store_manually(
    *,
    code: str,
    name: str,
    pack_id: str,
    timezone: str,
    pos_provider: str,
    owner_username: str,
    owner_password_hash: Optional[str],
    actor: str,
    run: str = "inline",
) -> Dict[str, Any]:
    """A master-created store without a sign-up (CLI ``provision``). With no
    password hash, the result carries a one-time ``setup_url``."""
    import zoneinfo

    from app.platform.codes import POS_PROVIDERS
    from app.platform.signup import USERNAME_RE

    norm = normalize_code(code)
    if norm is None:
        raise PlatformError(400, "invalid_code")
    if not (2 <= len((name or "").strip()) <= 80):
        raise PlatformError(400, "invalid", "The store name must be 2-80 characters.")
    try:
        zoneinfo.ZoneInfo(timezone)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        raise PlatformError(400, "invalid", "Unknown time zone.") from None
    if pos_provider not in POS_PROVIDERS:
        raise PlatformError(400, "invalid", "POS must be toast, clover or csv.")
    if not USERNAME_RE.match(owner_username or ""):
        raise PlatformError(400, "invalid", "Bad owner username.")
    pack_path(pack_id)
    with platform_session() as pdb:
        _check_code_free(pdb, norm)
        now = utcnow()
        req_id = None
        if owner_password_hash:
            # The hash travels like a sign-up's (one internal request row).
            req = SignupRequest(
                requested_code=norm, store_name=name.strip(), contact_name=owner_username, contact_email="-",
                timezone=timezone, pos_provider=pos_provider, owner_username=owner_username,
                owner_password_hash=owner_password_hash, status="approved",
                status_token_hash=_hash(secrets.token_urlsafe(16)), reviewed_by=actor, reviewed_at=now,
                review_note="created by a master", created_at=now,
            )
            pdb.add(req)
            pdb.flush()
            req_id = req.id
        store = Store(
            code=norm, name=name.strip(), status="pending", hosting="shared", db_name=db_name_for(norm),
            timezone=timezone, pos_provider=pos_provider, pack_id=pack_id, settings_version=1,
            signup_request_id=req_id, owner_username=owner_username, created_at=now, created_by=actor,
            approved_by=actor, approved_at=now, updated_at=now,
        )
        pdb.add(store)
        pdb.flush()
        if req_id is not None:
            pdb.get(SignupRequest, req_id).store_id = store.id
        audit("store.created", actor_type="platform_user", actor_name=actor, store_code=norm,
              details={"pack_id": pack_id, "timezone": timezone, "pos_provider": pos_provider}, db=pdb)
        pdb.commit()
    out: Dict[str, Any] = {"store_code": norm, "status": "provisioning", "login_url": login_url(norm)}
    if run == "inline":
        out.update(provision_store(norm, actor=actor))
    elif run == "background":
        start_provisioning(norm, actor=actor)
    return out


def retry_provisioning(code: str, *, actor: str, client_ip: Optional[str] = None, run: str = "background") -> Dict[str, Any]:
    with platform_session() as pdb:
        store = pdb.execute(select(Store).where(Store.code == code)).scalar_one_or_none()
        if store is None:
            raise PlatformError(404, "unknown_store")
        if store.status not in ("failed", "pending"):
            raise PlatformError(409, "conflict", f"Store {code} is {store.status}; only a failed store can be retried.")
        audit("store.provisioning_retried", actor_type="platform_user", actor_name=actor, store_code=code,
              details={"after_step": store.provisioning_step}, client_ip=client_ip, db=pdb)
        pdb.commit()
    if run == "inline":
        return provision_store(code, actor=actor)
    start_provisioning(code, actor=actor)
    return {"store_code": code, "status": "provisioning"}


def purge_store(code: str, *, actor: str) -> Dict[str, Any]:
    """Drop a failed or archived store's database and its platform rows.
    Never automatic: the CLI asks for ``--confirm CODE``. Upload files are
    left for a person to remove (the path is returned)."""
    with platform_session() as pdb:
        store = pdb.execute(select(Store).where(Store.code == code)).scalar_one_or_none()
        if store is None:
            raise PlatformError(404, "unknown_store")
        if store.status not in ("failed", "archived"):
            raise PlatformError(409, "conflict", "Only a failed or archived store can be purged.")
        if store.hosting != "shared" or store.db_host_ref:
            raise PlatformError(409, "conflict", "A dedicated store's database is purged on its own server.")
        db_name = store.db_name
    bridge.store_stopped(code)
    admin = make_engine(bridge.admin_url(), isolation_level="AUTOCOMMIT", pool_size=1, max_overflow=0)
    try:
        if _database_exists(admin, db_name):
            with admin.connect() as conn:
                conn.execute(text(f"DROP DATABASE {_quote_ident(db_name)} WITH (FORCE)"))
    finally:
        admin.dispose()
    with platform_session() as pdb:
        store = pdb.execute(select(Store).where(Store.code == code)).scalar_one()
        for req in pdb.execute(select(SignupRequest).where(SignupRequest.store_id == store.id)).scalars():
            req.store_id = None
        pdb.flush()
        pdb.delete(store)
        audit("store.purged", actor_type="platform_user", actor_name=actor, store_code=code,
              details={"db_name": db_name}, db=pdb)
        pdb.commit()
    return {"store_code": code, "dropped_database": db_name, "uploads_left_at": str(store_uploads_root(code))}
