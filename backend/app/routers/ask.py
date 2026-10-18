import anthropic
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.features import require_feature
from app.db import get_db
from app.demo import DEMO_ASK_ANSWER, is_demo_user
from app.models import User
from app.pos.gating import require_store_feature
from app.sales_access import can_see_sales
from app.schemas import AskRequest
from app.services.anthropic_client import AnthropicNotConfigured
from app.services.ask_bot import ask

# AI_ENABLED=false (per-store AI switch): 409 for everyone, like a POS gate.
router = APIRouter(
    prefix="/api/ask",
    tags=["ask"],
    dependencies=[Depends(require_feature("ask_bot")), Depends(require_store_feature("ai"))],
)


@router.post("")
def ask_question(body: AskRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty.")
    if is_demo_user(user):
        # App Review demo login: a fixed answer, no Anthropic call (no spend).
        return {"answer": DEMO_ASK_ANSWER, "model_used": "demo", "escalated": False, "tool_calls": [], "demo": True}
    history = [{"role": turn.role, "content": turn.content} for turn in body.history]
    try:
        # A "Hide sales numbers" user's bot has no sales tool and declines sales questions.
        return ask(db, body.question, history, include_sales=can_see_sales(user))
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
