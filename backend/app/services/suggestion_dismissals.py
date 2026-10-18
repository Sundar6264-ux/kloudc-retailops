"""Dismissed suggestions (migration 0049, models.SuggestionDismissal).

The three suggestion services (delivery_suggestions, invoice_close_suggestions,
schedule_suggestions) compute their suggestions on request; this module only
remembers which ones a person dismissed, so they stop showing. Undo deletes
the row and the suggestion shows again (if it still applies).
"""

import threading
from typing import Optional, Set

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import SuggestionDismissal, User
from app.timeutil import utcnow

KINDS = ("delivery", "invoice_close", "vendor_schedule")

_lock = threading.Lock()


def dismissed_keys(db: Session, kind: str) -> Set[str]:
    return set(
        db.execute(select(SuggestionDismissal.suggestion_key).where(SuggestionDismissal.kind == kind)).scalars()
    )


def dismiss(db: Session, kind: str, key: str, user: Optional[User]) -> SuggestionDismissal:
    """Idempotent: dismissing twice keeps the first row."""
    if kind not in KINDS:
        raise ValueError(f"Unknown suggestion kind {kind!r}")
    with _lock:
        row = db.execute(
            select(SuggestionDismissal).where(
                SuggestionDismissal.kind == kind, SuggestionDismissal.suggestion_key == key
            )
        ).scalars().first()
        if row is not None:
            return row
        row = SuggestionDismissal(
            kind=kind,
            suggestion_key=key,
            dismissed_by_user_id=user.id if user else None,
            dismissed_by_name=user.username if user else None,
            created_at=utcnow(),
        )
        db.add(row)
        try:
            db.commit()
        except IntegrityError:  # another process got there first
            db.rollback()
            row = db.execute(
                select(SuggestionDismissal).where(
                    SuggestionDismissal.kind == kind, SuggestionDismissal.suggestion_key == key
                )
            ).scalars().one()
        db.refresh(row)
        return row


def undismiss(db: Session, kind: str, key: str) -> bool:
    row = db.execute(
        select(SuggestionDismissal).where(SuggestionDismissal.kind == kind, SuggestionDismissal.suggestion_key == key)
    ).scalars().first()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True
