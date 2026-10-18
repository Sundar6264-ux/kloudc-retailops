"""Team rules - the in-app EULA (App Review guideline 1.2,
docs/APP_REVIEW_BUILD7_CONTRACT.md §3).

The build 7 app shows these once, before the Team tab opens, and records the
"I agree" here. The server never blocks Team endpoints on it: build 5/6 apps
don't know about the rules and must keep working.

Bump RULES_VERSION whenever RULES_TEXT changes in substance - everyone is then
asked to agree again.
"""

import datetime as dt
from typing import Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User
from app.team.models import TeamRulesAcceptance
from app.timeutil import utcnow

RULES_VERSION = 1
RULES_TITLE = "Team rules"
RULES_TEXT_TEMPLATE = """Team (tasks, announcements and chat) is for {store} staff to work together. By using it you agree to these rules.

Zero tolerance: there is no place here for objectionable content or abusive behaviour. Do not post anything offensive, hateful, threatening, harassing, sexual or discriminatory, and do not bully or abuse anyone - in messages, task comments, notes or photos. Offensive words are hidden automatically.

Report and block: anyone can report a message, a task or a task comment, and you can block a person so they can't message you.

How reports are handled: managers review every report within 24 hours. Content that breaks these rules is removed, and the person who posted it loses access to Team.

Violations: anyone who breaks these rules loses access to Team and may lose their account.

Questions or concerns: {contact}"""


def rules_text() -> str:
    """The rules for this store (STORE_NAME, STORE_PHONE)."""
    from app.store import settings_view as sv

    phone = sv.store_phone()
    contact = f"ask your manager or call {phone}." if phone else "ask your manager."
    return RULES_TEXT_TEMPLATE.format(store=sv.store_display_name(), contact=contact)


def __getattr__(name):
    if name == "RULES_TEXT":
        return rules_text()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _iso(value: Optional[dt.datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _accepted(db: Session, user: User, version: int) -> Optional[TeamRulesAcceptance]:
    return db.get(TeamRulesAcceptance, (user.id, version))


def rules_out(db: Session, user: User) -> dict:
    row = _accepted(db, user, RULES_VERSION)
    return {
        "version": RULES_VERSION,
        "title": RULES_TITLE,
        "text": rules_text(),
        "accepted": row is not None,
        "accepted_at": _iso(row.accepted_at) if row else None,
    }


def accept(db: Session, user: User, version: int) -> dict:
    """Idempotent: agreeing again returns the first acceptance's time."""
    if version != RULES_VERSION:
        raise HTTPException(
            status_code=400, detail="These rules have been updated - please read the latest version."
        )
    row = _accepted(db, user, version)
    if row is None:
        row = TeamRulesAcceptance(user_id=user.id, version=version, accepted_at=utcnow())
        db.add(row)
        try:
            db.commit()
        except IntegrityError:  # a double-tapped "I agree" racing itself
            db.rollback()
            row = _accepted(db, user, version)
    return {"accepted": True, "accepted_at": _iso(row.accepted_at)}
