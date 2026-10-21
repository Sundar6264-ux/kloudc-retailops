import logging

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.features import require_feature
from app.db import get_db
from app.demo import is_demo_user
from app.models import User
from app.sales_access import can_see_sales
from app.services.anthropic_client import AnthropicNotConfigured
from app.services.weekly_digest import generate_weekly_digest

router = APIRouter(prefix="/api/weekly-digest", tags=["weekly-digest"], dependencies=[Depends(require_feature("overview"))])


@router.post("/generate")
def generate(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        # A "Hide sales numbers" user gets the digest without its sales section.
        # An App Review demo login gets the data with a fixed narrative - no
        # Anthropic call (app/demo.py).
        return generate_weekly_digest(db, include_sales=can_see_sales(user), demo=is_demo_user(user))
    except AnthropicNotConfigured:
        raise HTTPException(
            status_code=500,
            detail="AI isn't set up for this store yet. An admin can add the AI key in Setup › Integrations.",
        )
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=500, detail="The store's AI key was refused. An admin can check it in Setup › Integrations.")
    except anthropic.APIError as exc:
        logging.getLogger(__name__).warning("AI request failed: %s", exc)
        raise HTTPException(status_code=502, detail="The AI service didn't answer. Try again in a minute.")
