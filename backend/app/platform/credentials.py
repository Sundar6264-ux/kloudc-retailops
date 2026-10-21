"""Store credentials vault (contract 8.3, 9; owner C).

One row per (store, kind) in the platform database. Public fields are
stored as JSON and shown to the store admin; secret fields are encrypted
together (AES-256-GCM, bound to store + kind, ``app.platform.crypto``) and
never leave this module except as Settings values for the store's own
connectors (``settings_overrides``, called by track B's settings overlay).

A blank or missing secret on save keeps the stored one ("write-only"
inputs); ``clear`` names secret fields to remove.
"""

import datetime as dt
import ipaddress
import json
import re
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform import crypto
from app.platform.audit import audit
from app.platform.models import Store, StoreCredential, utcnow

logger = logging.getLogger(__name__)


class CredentialError(ValueError):
    """A bad value on save (400). ``field``: the form field it is about."""

    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(message)
        self.field = field


class UnknownKind(KeyError):
    pass


class StoreNotFound(LookupError):
    pass


_HOST_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*")


def host_problem(host: Any) -> Optional[str]:
    """Why `host` may not be an integration's address, or None.

    The shared service connects to these addresses for every store (Test
    connection, syncs, sends), so a store's settings must not point it at
    the server itself or its private network: no localhost, no private /
    loopback / link-local / reserved IP literal, and a name must end in a
    real top-level label (refuses ``127.1``, ``2130706433``, ``0x7f.1``,
    which resolvers read as loopback). Names are also checked after DNS by
    the connection tests (``connector_tests``)."""
    h = str(host or "").strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    h = h.rstrip(".")
    if not h:
        return "is empty"
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        ip = None
    if ip is not None:
        return None if ip.is_global else "is a private or local address"
    if h == "localhost" or h.endswith(".localhost") or h.endswith(".local") or h.endswith(".internal"):
        return "is a private or local address"
    if len(h) > 253 or not _HOST_RE.fullmatch(h):
        return "is not a valid host name"
    if not any(c.isalpha() for c in h.rsplit(".", 1)[-1]):
        return "is not a valid host name"
    return None


def _https_url(value: Any, name: str) -> str:
    v = str(value or "").strip().rstrip("/")
    parts = urlsplit(v)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise CredentialError(f"{name} must be an https:// URL")
    problem = host_problem(parts.hostname)
    if problem:
        raise CredentialError(f"{name}: that address {problem}")
    from app.config import refused_host

    if refused_host(v):
        raise CredentialError(f"{name} may not point at that domain")
    return v


def _text(value: Any, name: str, max_len: int = 200) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int, float)):
        raise CredentialError(f"{name} must be text")
    v = str(value).strip()
    if len(v) > max_len:
        raise CredentialError(f"{name} is longer than {max_len} characters")
    if any(ord(c) < 32 for c in v):
        raise CredentialError(f"{name} has control characters")
    return v


def _mail_host(value: Any, name: str) -> str:
    v = _text(value, name)
    problem = host_problem(v)
    if problem:
        raise CredentialError(f"{name}: that address {problem}")
    return v.lower().rstrip(".")


def _port(value: Any, name: str) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise CredentialError(f"{name} must be a whole number") from None
    if not 1 <= n <= 65535:
        raise CredentialError(f"{name} must be 1-65535")
    return n


def _bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0", "yes", "no"):
        return value.strip().lower() in ("true", "1", "yes")
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise CredentialError(f"{name} must be true or false")


def _chat_ids(value: Any, name: str) -> Dict[str, str]:
    if value in (None, ""):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise CredentialError(f"{name} must be a JSON object of role -> chat id") from None
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise CredentialError(f"{name} must be an object of role -> chat id")
    if len(value) > 50:
        raise CredentialError(f"{name} has too many entries")
    return {k.strip()[:64]: v.strip()[:128] for k, v in value.items() if k.strip()}


def _choice(*choices: str):
    def check(value: Any, name: str) -> str:
        v = str(value or "").strip().lower()
        if v not in choices:
            raise CredentialError(f"{name} must be one of {', '.join(choices)}")
        return v

    return check


@dataclass(frozen=True)
class FieldSpec:
    name: str
    check: Callable[[Any, str], Any] = _text
    default: Any = ""
    required: bool = False
    label: str = ""  # as the Integrations form labels it (plain messages)


@dataclass(frozen=True)
class CredentialSpec:
    kind: str
    label: str
    public: Tuple[FieldSpec, ...]
    secrets: Tuple[str, ...]
    primary_secret: str
    # Settings field <- ("public"|"secret", field name)
    settings_map: Tuple[Tuple[str, str, str], ...] = ()
    testable: bool = True
    extra: Mapping[str, Any] = field(default_factory=dict)
    # Field name -> the form's label, for secrets (plain messages).
    secret_labels: Mapping[str, str] = field(default_factory=dict)
    # One plain sentence when a required field or the primary secret is
    # missing (else "Fill in the <label> for <kind>.").
    required_message: str = ""


# Toast gives a store two key pairs (Toast Web > Integrations > Toast API
# access): the main pair (orders, menus, stock) and a separate Analytics pair
# (enterprise-metrics scope) that re-checks each day's sales totals.
TOAST_MAIN_KEYS_MISSING = (
    "Fill in the main Toast keys first (Client ID, Client secret and Restaurant ID). "
    "The Analytics keys are an extra."
)
SAME_KEYS_WARNING = (
    "These look like the same keys as the main ones. Toast gives Analytics its own Client ID and secret."
)

KINDS: Dict[str, CredentialSpec] = {
    "toast": CredentialSpec(
        kind="toast",
        label="Toast",
        public=(
            FieldSpec("client_id", required=True, label="Client ID"),
            FieldSpec("restaurant_guid", required=True, label="Restaurant ID (GUID)"),
            FieldSpec("api_base", _https_url, "https://ws-api.toasttab.com", label="API address"),
            FieldSpec("analytics_client_id", label="Analytics client ID"),
        ),
        secrets=("client_secret", "analytics_client_secret"),
        primary_secret="client_secret",
        secret_labels={"client_secret": "Client secret", "analytics_client_secret": "Analytics client secret"},
        required_message=TOAST_MAIN_KEYS_MISSING,
        settings_map=(
            ("toast_client_id", "public", "client_id"),
            ("toast_restaurant_guid", "public", "restaurant_guid"),
            ("toast_api_base", "public", "api_base"),
            ("toast_analytics_client_id", "public", "analytics_client_id"),
            ("toast_client_secret", "secret", "client_secret"),
            ("toast_analytics_client_secret", "secret", "analytics_client_secret"),
        ),
    ),
    # Website-ready track D: "token" = a merchant-generated API token (one
    # merchant, manual mode); "oauth" = Connect with Clover (OAuth v2: a
    # 30-minute access token + refresh token, the pair replaced atomically by
    # refresh_token_pair). The connector reads these itself
    # (app/pos/adapters/clover/auth.py), so there is no settings_map.
    "clover": CredentialSpec(
        kind="clover",
        label="Clover",
        public=(
            FieldSpec("merchant_id"),
            FieldSpec("region", _choice("us", "eu", "la", "sandbox"), "us"),
            FieldSpec("mode", _choice("token", "oauth"), "token"),
            FieldSpec("token_expires_at"),
            FieldSpec("merchant_name"),
        ),
        secrets=("access_token", "refresh_token"),
        primary_secret="access_token",
        settings_map=(),
        testable=True,
    ),
    "whatsapp": CredentialSpec(
        kind="whatsapp",
        label="WhatsApp",
        public=(
            FieldSpec("base_url", _https_url, "https://gate.whapi.cloud"),
            FieldSpec("group_chat_ids", _chat_ids, {}),
        ),
        secrets=("api_token",),
        primary_secret="api_token",
        settings_map=(
            ("whapi_base_url", "public", "base_url"),
            ("whatsapp_group_chat_ids", "public", "group_chat_ids"),
            ("whapi_api_token", "secret", "api_token"),
        ),
    ),
    "smtp": CredentialSpec(
        kind="smtp",
        label="Email (SMTP)",
        public=(
            FieldSpec("host", _mail_host, required=True, label="Server"),
            FieldSpec("port", _port, 465),
            FieldSpec("ssl", _bool, True),
            FieldSpec("username", required=True, label="Username"),
            FieldSpec("from_name"),
        ),
        secrets=("password",),
        primary_secret="password",
        secret_labels={"password": "Password / app password"},
        settings_map=(
            ("smtp_host", "public", "host"),
            ("smtp_port", "public", "port"),
            ("smtp_ssl", "public", "ssl"),
            ("smtp_username", "public", "username"),
            ("email_from_name", "public", "from_name"),
            ("smtp_password", "secret", "password"),
        ),
    ),
    "ai": CredentialSpec(
        kind="ai",
        label="AI (Anthropic)",
        public=(),
        secrets=("api_key",),
        primary_secret="api_key",
        settings_map=(("anthropic_api_key", "secret", "api_key"),),
    ),
}

# Credential kinds that are the store's POS: saving one re-runs its catch-up sync.
POS_KINDS = frozenset({"toast", "clover"})
MAX_SECRET_LEN = 4096

# Public fields that say WHERE a kind's secrets are sent. When one changes,
# the saved secrets are not carried to the new address (save and Test
# connection): whoever changes the address types the secret again, so a
# stored secret can never be redirected to a server of someone's choosing.
ADDRESS_FIELDS: Dict[str, Tuple[str, ...]] = {"toast": ("api_base",), "whatsapp": ("base_url",), "smtp": ("host",)}


def _address(kind: str, public: Optional[Mapping[str, Any]], name: str) -> str:
    default = next((f.default for f in KINDS[kind].public if f.name == name), "")
    value = (public or {}).get(name, default)
    return str(value or default or "").strip().rstrip("/").rstrip(".").lower()


def address_changed(kind: str, old_public: Optional[Mapping[str, Any]], new_public: Mapping[str, Any]) -> bool:
    """True when `new_public` sends this kind's secrets somewhere else."""
    return any(_address(kind, old_public, f) != _address(kind, new_public, f) for f in ADDRESS_FIELDS.get(kind, ()))


def spec(kind: str) -> CredentialSpec:
    try:
        return KINDS[kind]
    except KeyError:
        raise UnknownKind(kind) from None


@dataclass
class CredentialView:
    kind: str
    label: str
    configured: bool
    status: str
    last4: Optional[str]
    public: Dict[str, Any]
    updated_at: Optional[str]
    updated_by: Optional[str]
    last_tested_at: Optional[str]
    last_test_message: Optional[str]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "configured": self.configured,
            "status": self.status,
            "last4": self.last4,
            "public": self.public,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
            "last_tested_at": self.last_tested_at,
            "last_test_message": self.last_test_message,
        }


def _iso(value: Optional[dt.datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _store(db: Session, code: str) -> Store:
    store = db.execute(select(Store).where(Store.code == code)).scalar_one_or_none()
    if store is None:
        raise StoreNotFound(code)
    return store


def _row(db: Session, store: Store, kind: str) -> Optional[StoreCredential]:
    return db.execute(
        select(StoreCredential).where(StoreCredential.store_id == store.id, StoreCredential.kind == kind)
    ).scalar_one_or_none()


def clean_public(kind: str, public: Mapping[str, Any], existing: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Validate the public fields; missing ones keep ``existing`` or default."""
    sp = spec(kind)
    public = dict(public or {})
    unknown = sorted(set(public) - {f.name for f in sp.public})
    if unknown:
        raise CredentialError(f"unknown field(s) for {sp.label}: {', '.join(unknown)}")
    out: Dict[str, Any] = {}
    for f in sp.public:
        if f.name in public:
            raw = public[f.name]
        elif existing is not None and f.name in existing:
            raw = existing[f.name]
        else:
            raw = f.default
        if isinstance(raw, str) and not raw.strip():
            raw = None
        if raw is None:
            if f.required:
                raise _missing(sp, f.name, f.label)
            raw = f.default
        value = f.check(raw, f.label or f.name) if raw not in (None, "") else ""
        if f.required and value in ("", None):
            raise _missing(sp, f.name, f.label)
        out[f.name] = value
    return out


def _missing(sp: CredentialSpec, name: str, label: str = "") -> CredentialError:
    """A required field or the primary secret is empty: one plain sentence."""
    if sp.required_message:
        return CredentialError(sp.required_message, field=name)
    label = label or sp.secret_labels.get(name) or name.replace("_", " ").capitalize()
    return CredentialError(f"Fill in the {label} for {sp.label}.", field=name)


def secret_label(kind: str, name: str) -> str:
    """A secret's name as the form shows it ("Client secret")."""
    sp = spec(kind)
    return (sp.secret_labels.get(name) or name.replace("_", " ")).lower() if name else ""


def _same(a: Any, b: Any) -> bool:
    a, b = str(a or "").strip(), str(b or "").strip()
    return bool(a) and a.lower() == b.lower()


def credential_warnings(kind: str, public: Mapping[str, Any], secrets: Mapping[str, Any]) -> List[str]:
    """Plain warnings that don't stop a save (contract 13.5 "warnings"):
    for Toast, Analytics keys that repeat the main ones or are half a pair.
    Compares secrets only to each other; never returns a value."""
    if kind != "toast":
        return []
    public = public or {}
    secrets = secrets or {}
    a_id = str(public.get("analytics_client_id") or "").strip()
    a_secret = str(secrets.get("analytics_client_secret") or "").strip()
    out: List[str] = []
    if _same(a_id, public.get("client_id")) or (a_secret and a_secret == str(secrets.get("client_secret") or "").strip()):
        out.append(SAME_KEYS_WARNING)
    if a_id and not a_secret:
        out.append("Add the Analytics client secret too. The Analytics keys only work as a pair.")
    elif a_secret and not a_id:
        out.append("Add the Analytics client ID too. The Analytics keys only work as a pair.")
    return out


def default_public(kind: str) -> Dict[str, Any]:
    """The public fields of a kind with their defaults (nothing saved yet)."""
    return {f.name: f.default for f in spec(kind).public}


def clean_secrets(kind: str, secrets_in: Mapping[str, Any]) -> Dict[str, str]:
    """Only the non-blank secrets given (blank = keep)."""
    sp = spec(kind)
    secrets_in = dict(secrets_in or {})
    unknown = sorted(set(secrets_in) - set(sp.secrets))
    if unknown:
        raise CredentialError(f"unknown secret field(s) for {sp.label}: {', '.join(unknown)}")
    out = {}
    for name, value in secrets_in.items():
        if value is None:
            continue
        if not isinstance(value, str):
            raise CredentialError(f"{name} must be text")
        v = value.strip()
        if not v:
            continue
        if len(v) > MAX_SECRET_LEN or any(ord(c) < 32 for c in v):
            raise CredentialError(f"{name} is not a valid value")
        out[name] = v
    return out


def _last4(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return value[-4:] if len(value) >= 8 else None


def _decrypt_row(code: str, row: StoreCredential) -> Dict[str, str]:
    raw = crypto.decrypt(row.key_id, row.nonce, row.ciphertext, crypto.aad_for(code, row.kind))
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise crypto.DecryptError("not an object")
    return {str(k): str(v) for k, v in data.items()}


def _decrypt_or_none(code: str, row: StoreCredential, *, report: bool = True) -> Optional[Dict[str, str]]:
    try:
        return _decrypt_row(code, row)
    except (crypto.DecryptError, crypto.KeyRingError, ValueError) as exc:
        if report:
            logger.warning("credential %s/%s does not decrypt: %s", code, row.kind, type(exc).__name__)
            audit(
                "credential.decrypt_failed",
                actor_type="system",
                store_code=code,
                target=row.kind,
                details={"key_id": row.key_id, "reason": type(exc).__name__},
            )
        return None


def _view(code: str, kind: str, row: Optional[StoreCredential], *, decrypt_check: bool = True) -> CredentialView:
    sp = spec(kind)
    if row is None:
        return CredentialView(kind, sp.label, False, "not_configured", None, default_public(kind), None, None, None, None)
    try:
        public = json.loads(row.public_fields or "{}")
    except ValueError:
        public = {}
    configured = True
    status = row.status
    if decrypt_check:
        secrets_now = _decrypt_or_none(code, row)
        if secrets_now is None:
            configured, status = False, "not_configured"
        elif sp.primary_secret not in secrets_now:
            configured = False
    return CredentialView(
        kind=kind,
        label=sp.label,
        configured=configured,
        status=status,
        last4=row.last4 if configured else None,
        public=public,
        updated_at=_iso(row.updated_at),
        updated_by=row.updated_by,
        last_tested_at=_iso(row.last_tested_at),
        last_test_message=row.last_test_message,
    )


def _bump(store: Store) -> None:
    store.settings_version = int(store.settings_version or 1) + 1
    store.updated_at = utcnow()


# --- public API (contract 8.3) ------------------------------------------------


def save_credential(
    code: str,
    kind: str,
    public: Mapping[str, Any],
    secrets: Mapping[str, Any],
    *,
    actor: str,
    clear: Sequence[str] = (),
    actor_type: str = "store_user",
    client_ip: Optional[str] = None,
    db: Optional[Session] = None,
) -> CredentialView:
    from app.platform.db import platform_session

    sp = spec(kind)
    clear = list(clear or [])
    bad = [c for c in clear if c not in sp.secrets]
    if bad:
        raise CredentialError(f"cannot clear unknown field(s): {', '.join(bad)}")
    new_secrets = clean_secrets(kind, secrets)

    def _do(s: Session) -> CredentialView:
        store = _store(s, code)
        row = _row(s, store, kind)
        existing_public = None
        existing_secrets: Dict[str, str] = {}
        if row is not None:
            try:
                existing_public = json.loads(row.public_fields or "{}")
            except ValueError:
                existing_public = {}
            existing_secrets = _decrypt_or_none(code, row) or {}
        merged_public = clean_public(kind, public, existing_public)
        merged = {k: v for k, v in existing_secrets.items() if k in sp.secrets and k not in clear}
        moved = bool(merged) and address_changed(kind, existing_public, merged_public)
        if moved:
            # A new address: only the secrets typed now go there.
            merged = {}
        merged.update(new_secrets)
        if sp.primary_secret not in merged and kind != "clover":
            if moved:
                raise CredentialError(
                    f"Enter the {secret_label(kind, sp.primary_secret)} again: the address changed, "
                    "so the saved one is not sent there.",
                    field=sp.primary_secret,
                )
            raise _missing(sp, sp.primary_secret)
        sealed = crypto.encrypt(
            json.dumps(merged, sort_keys=True).encode("utf-8"), crypto.aad_for(code, kind)
        )
        now = utcnow()
        changed_public = sorted(k for k in merged_public if (existing_public or {}).get(k) != merged_public[k])
        if row is None:
            row = StoreCredential(store_id=store.id, kind=kind, created_at=now)
            s.add(row)
        row.public_fields = json.dumps(merged_public, sort_keys=True)
        row.ciphertext = sealed["ciphertext"]
        row.nonce = sealed["nonce"]
        row.key_id = sealed["key_id"]
        row.last4 = _last4(merged.get(sp.primary_secret))
        if new_secrets or clear or changed_public or row.status is None:
            row.status = "untested"
            row.last_test_message = None
        row.updated_by = actor[:128]
        row.updated_at = now
        _bump(store)
        audit(
            "credential.saved",
            actor_type=actor_type,
            actor_name=actor,
            store_code=code,
            target=kind,
            details={
                "public_changed": changed_public,
                "secrets_changed": sorted(new_secrets),
                "secrets_cleared": sorted(clear),
            },
            client_ip=client_ip,
            db=s,
        )
        s.commit()
        return _view(code, kind, row, decrypt_check=False)

    if db is not None:
        view = _do(db)
    else:
        with platform_session() as s:
            view = _do(s)
    from app.platform.bridge import pos_keys_saved, store_changed

    store_changed(code)
    if kind in POS_KINDS:
        pos_keys_saved(code)
    return view


def credential_view(code: str, kind: str, db: Optional[Session] = None) -> CredentialView:
    from app.platform.db import platform_session

    spec(kind)
    if db is not None:
        return _view(code, kind, _row(db, _store(db, code), kind))
    with platform_session() as s:
        return _view(code, kind, _row(s, _store(s, code), kind))


def list_credentials(code: str, db: Optional[Session] = None) -> List[CredentialView]:
    from app.platform.db import platform_session

    def _do(s: Session) -> List[CredentialView]:
        store = _store(s, code)
        rows = {r.kind: r for r in s.execute(select(StoreCredential).where(StoreCredential.store_id == store.id)).scalars()}
        return [_view(code, k, rows.get(k)) for k in KINDS]

    if db is not None:
        return _do(db)
    with platform_session() as s:
        return _do(s)


def configured_kinds(code: str, db: Session) -> List[str]:
    store = _store(db, code)
    return sorted(db.execute(select(StoreCredential.kind).where(StoreCredential.store_id == store.id)).scalars())


def delete_credential(
    code: str, kind: str, *, actor: str, actor_type: str = "store_user", client_ip: Optional[str] = None
) -> bool:
    from app.platform.db import platform_session

    spec(kind)
    with platform_session() as s:
        store = _store(s, code)
        row = _row(s, store, kind)
        if row is None:
            return False
        s.delete(row)
        _bump(store)
        audit(
            "credential.deleted", actor_type=actor_type, actor_name=actor, store_code=code, target=kind,
            client_ip=client_ip, db=s,
        )
        s.commit()
    from app.platform.bridge import store_changed

    store_changed(code)
    return True


def decrypt_secrets(code: str, kind: str) -> Dict[str, str]:
    """Internal: the secret fields of one credential ({} when none or
    undecryptable). Never log or return this to a client."""
    from app.platform.db import platform_session

    spec(kind)
    with platform_session() as s:
        row = _row(s, _store(s, code), kind)
        if row is None:
            return {}
        return _decrypt_or_none(code, row) or {}


def stored_values(code: str, kind: str) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """(public, secrets) of the saved credential, for "Test connection"."""
    from app.platform.db import platform_session

    with platform_session() as s:
        row = _row(s, _store(s, code), kind)
        if row is None:
            return default_public(kind), {}
        try:
            public = json.loads(row.public_fields or "{}")
        except ValueError:
            public = {}
        return public, (_decrypt_or_none(code, row) or {})


def record_test_result(
    code: str, kind: str, ok: bool, message: str, *, actor: str, actor_type: str = "store_user",
    client_ip: Optional[str] = None, saved: bool = True,
) -> None:
    """Store the result on the saved row (``saved``: the test used the stored
    values) and audit it."""
    from app.platform.db import platform_session

    with platform_session() as s:
        store = _store(s, code)
        if saved:
            row = _row(s, store, kind)
            if row is not None:
                row.status = "ok" if ok else "failed"
                row.last_tested_at = utcnow()
                row.last_test_message = message[:500]
        audit(
            "credential.tested", actor_type=actor_type, actor_name=actor, store_code=code, target=kind,
            details={"ok": bool(ok), "saved_values": bool(saved)}, client_ip=client_ip, db=s,
        )
        s.commit()


def settings_overrides(code: str, db: Optional[Session] = None) -> Dict[str, Any]:
    """Decrypted credentials of one store as Settings field values (track B's
    overlay, contract 8.1). A row that does not decrypt is left out (as if
    not configured) and audited."""
    from app.platform.db import platform_session

    def _do(s: Session) -> Dict[str, Any]:
        store = s.execute(select(Store).where(Store.code == code)).scalar_one_or_none()
        if store is None:
            return {}
        out: Dict[str, Any] = {}
        for row in s.execute(select(StoreCredential).where(StoreCredential.store_id == store.id)).scalars():
            sp = KINDS.get(row.kind)
            if sp is None or not sp.settings_map:
                continue
            secrets_now = _decrypt_or_none(code, row)
            if secrets_now is None:
                continue
            try:
                public = json.loads(row.public_fields or "{}")
            except ValueError:
                public = {}
            for setting, where, name in sp.settings_map:
                value = public.get(name) if where == "public" else secrets_now.get(name)
                if value is None:
                    continue
                if setting == "whatsapp_group_chat_ids":
                    value = json.dumps(value, sort_keys=True) if value else ""
                out[setting] = value
        return out

    if db is not None:
        return _do(db)
    with platform_session() as s:
        return _do(s)


def rotate_all(*, actor: str = "system", dry_run: bool = False) -> Dict[str, int]:
    """Re-encrypt every credential (and TOTP secret) with the active key."""
    from app.platform.db import platform_session
    from app.platform.models import PlatformUser

    crypto.reset_keyring()
    ring = crypto.keyring()
    counts = {"credentials": 0, "credentials_failed": 0, "totp": 0, "totp_failed": 0, "already_current": 0}
    with platform_session() as s:
        stores = {st.id: st.code for st in s.execute(select(Store)).scalars()}
        for row in s.execute(select(StoreCredential)).scalars():
            code = stores.get(row.store_id)
            if row.key_id == ring.active_id:
                counts["already_current"] += 1
                continue
            try:
                plain = crypto.decrypt(row.key_id, row.nonce, row.ciphertext, crypto.aad_for(code, row.kind), ring)
            except crypto.DecryptError:
                counts["credentials_failed"] += 1
                continue
            sealed = crypto.encrypt(plain, crypto.aad_for(code, row.kind), ring)
            if not dry_run:
                row.ciphertext, row.nonce, row.key_id = sealed["ciphertext"], sealed["nonce"], sealed["key_id"]
            counts["credentials"] += 1
        for user in s.execute(select(PlatformUser).where(PlatformUser.totp_secret_enc.is_not(None))).scalars():
            if crypto.sealed_key_id(user.totp_secret_enc) == ring.active_id:
                counts["already_current"] += 1
                continue
            try:
                secret = crypto.open_text(user.totp_secret_enc, crypto.totp_aad(user.username))
            except crypto.DecryptError:
                counts["totp_failed"] += 1
                continue
            if not dry_run:
                user.totp_secret_enc = crypto.seal_text(secret, crypto.totp_aad(user.username))
            counts["totp"] += 1
        if not dry_run:
            audit("keys.rotated", actor_type="system", actor_name=actor, details={**counts, "key_id": ring.active_id}, db=s)
            s.commit()
    return counts


def refresh_token_pair(
    code: str,
    kind: str,
    needs_refresh: Callable[[Dict[str, Any], Dict[str, str]], bool],
    do_refresh: Callable[[Dict[str, Any], Dict[str, str]], Tuple[Dict[str, Any], Dict[str, str]]],
) -> Tuple[Dict[str, Any], Dict[str, str]]:
    """Replace an OAuth token pair atomically (website-ready track D3).

    Locks the credential row (``SELECT ... FOR UPDATE`` on PostgreSQL), asks
    ``needs_refresh(public, secrets)`` again under the lock - another process
    may have refreshed meanwhile - and only then calls ``do_refresh``, which
    returns (public changes, new secrets). Both secrets are written in one
    encrypted value in the same transaction, so a crash never leaves a new
    access token with an old (already used) refresh token. Not audited (it
    runs every 30 minutes); no catch-up sync is triggered. Returns the
    (public, secrets) now stored."""
    from app.platform.db import platform_session

    sp = spec(kind)
    with platform_session() as s:
        store = _store(s, code)
        row = s.execute(
            select(StoreCredential)
            .where(StoreCredential.store_id == store.id, StoreCredential.kind == kind)
            .with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise StoreNotFound(f"{code}/{kind}")
        try:
            public = json.loads(row.public_fields or "{}")
        except ValueError:
            public = {}
        secrets_now = _decrypt_or_none(code, row) or {}
        if not needs_refresh(public, secrets_now):
            s.rollback()
            return public, secrets_now
        public_changes, new_secrets = do_refresh(public, secrets_now)
        merged_public = clean_public(kind, {**public, **(public_changes or {})}, public)
        merged = {k: v for k, v in {**secrets_now, **clean_secrets(kind, new_secrets)}.items() if k in sp.secrets}
        sealed = crypto.encrypt(json.dumps(merged, sort_keys=True).encode("utf-8"), crypto.aad_for(code, kind))
        row.public_fields = json.dumps(merged_public, sort_keys=True)
        row.ciphertext, row.nonce, row.key_id = sealed["ciphertext"], sealed["nonce"], sealed["key_id"]
        row.last4 = _last4(merged.get(sp.primary_secret))
        row.updated_at = utcnow()
        s.commit()
        return merged_public, merged
