"""App Review demo accounts (DEMO_USERNAMES, docs/APP_REVIEW_BUILD7_CONTRACT.md),
and the DEMO store's DEMO_MODE (every login is a demo login).

The ONE place that decides whether a login is a demo account. A demo user
can look at everything its features allow, but nothing it does may reach
the outside world: no WhatsApp, no Toast email, no Anthropic call, no
label print, no push to anyone outside its own teams.
"""

from typing import Optional, Set

from sqlalchemy import and_, false, func, not_, or_, select, true
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User

# Recorded as the per-channel error text of a demo submission (same shape as
# the "not configured" path); invoice_submission treats it as done, never
# failed.
DEMO_NOT_SENT = "Demo account - not sent"
DEMO_ASK_ANSWER = "Ask Inventory Bot is turned off for the demo account."
DEMO_CANT_DELETE = "The demo account can't be deleted."


def demo_usernames() -> Set[str]:
    raw = get_settings().demo_usernames or ""
    return {n.strip().lower() for n in raw.split(",") if n.strip()}


def is_demo_username(username: Optional[str]) -> bool:
    return bool(username) and username.strip().lower() in demo_usernames()


def demo_mode() -> bool:
    """The DEMO store (DEMO_MODE, multi-store plan 3.1a, M2 MT13): every login
    is treated as a demo login. Read with getattr so this works before the
    setting exists (contract rule 8)."""
    return bool(getattr(get_settings(), "demo_mode", False))


def is_demo_user(user: Optional[User]) -> bool:
    """A demo login: its username is in DEMO_USERNAMES, or this server is the
    DEMO store (then every login is). Only who is *acting* changes with
    DEMO_MODE - which rows count as demo-made (demo_user_ids /
    demo_made_clause) still follows DEMO_USERNAMES, so the store's made-up
    data stays visible."""
    return user is not None and (demo_mode() or is_demo_username(user.username))


def demo_user_ids(db: Session) -> Set[int]:
    names = demo_usernames()
    if not names:
        return set()
    rows = db.execute(select(User.id, User.username)).all()
    return {uid for uid, name in rows if (name or "").strip().lower() in names}


# --- Hiding demo-made records from real staff -----------------------------------------
# A demo login's own submissions (invoices, checklists) are sandboxed: real
# staff never see them in lists, reports, home/today data, reorder/vendor
# delivery logic or alerts. The demo user still sees its own.


def demo_made_clause(db: Session, user_id_col, name_col=None):
    """SQL condition: the row was made by a demo account. Matched on the
    user id, and (for a row whose account was later deleted) on the
    denormalized username - but never for user id NULL/0 rows (a public
    checklist link's picked name could be anything)."""
    conds = []
    ids = demo_user_ids(db)
    if ids:
        # coalesce: a NULL id must compare False, not NULL (NOT NULL would drop the row).
        conds.append(func.coalesce(user_id_col, 0).in_(sorted(ids)))
    names = demo_usernames()
    if name_col is not None and names:
        conds.append(
            and_(
                user_id_col.is_not(None),
                user_id_col != 0,
                func.lower(func.trim(func.coalesce(name_col, ""))).in_(sorted(names)),
            )
        )
    return or_(*conds) if conds else false()


def hide_demo_clause(
    db: Session, user_id_col, name_col=None, viewer: Optional[User] = None, include_demo: bool = False
):
    """SQL condition for a list query: keeps real rows, drops demo-made ones.
    - viewer is a demo user: its own rows stay (other demo logins' don't);
    - viewer is an admin who asked for `include_demo`: nothing is dropped;
    - everyone else, background jobs and store-wide logic (viewer None): every
      demo-made row is dropped."""
    if viewer is not None and include_demo and viewer.is_admin and not is_demo_user(viewer):
        return true()
    demo = demo_made_clause(db, user_id_col, name_col)
    if viewer is not None and is_demo_user(viewer):
        return or_(not_(demo), user_id_col == viewer.id)
    return not_(demo)


def hidden_from(db: Session, viewer: Optional[User], user_id: Optional[int], name: Optional[str]) -> bool:
    """Python twin of hide_demo_clause for a single row (detail endpoints):
    True when `viewer` must not see a row made by (user_id, name). Admins
    can still open one by id."""
    made_by_demo = user_id is not None and user_id != 0 and (
        user_id in demo_user_ids(db) or is_demo_username(name)
    )
    if not made_by_demo:
        return False
    if viewer is None:
        return True
    if is_demo_user(viewer):
        return user_id != viewer.id
    return not viewer.is_admin
