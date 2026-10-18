"""GET /api/glossary - every status / tag / label the website and the app
show, with a plain-English definition and the exact rule (see app/glossary.py).
Any logged-in user; no feature grant needed."""

from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.glossary import get_glossary

router = APIRouter(prefix="/api/glossary", tags=["glossary"], dependencies=[Depends(get_current_user)])


@router.get("")
def glossary() -> dict:
    return get_glossary()
