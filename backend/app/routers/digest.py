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
            detail="ANTHROPIC_API_KEY isn't configured on the server - set it in backend/.env and "
            "restart the service.",
        )
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY was rejected by the Claude API.")
    except anthropic.APIError as exc:
        raise HTTPException(status_code=502, detail=f"Claude API error: {exc}")
