from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.features import require_feature
from app.db import get_db
from app.models import User
from app.schemas import TransferConfirmRequest
from app.services.location_transfer import get_transfer_candidates, confirm_transfers

router = APIRouter(prefix="/api/transfers", tags=["transfers"], dependencies=[Depends(require_feature("transfer_review"))])


@router.get("/candidates")
def transfer_candidates(
    direction: str = Query(..., pattern="^(container_to_store|store_to_container)$"),
    db: Session = Depends(get_db),
):
    result = get_transfer_candidates(db, direction)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Not enough inventory upload history yet to compare against (need at least two "
            "snapshot dates).",
        )
    return result


@router.post("/confirm")
def transfer_confirm(
    payload: TransferConfirmRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    logged = confirm_transfers(
        db,
        direction=payload.direction,
        transfer_date=payload.transfer_date,
        items=[item.model_dump() for item in payload.items],
        logged_by_user_id=user.id,
    )
    return {"logged": logged}
