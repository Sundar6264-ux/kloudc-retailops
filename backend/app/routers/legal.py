"""GET /api/app/legal - legal links and the AI consent notice (MT13).

Public (no login, never 401): the app shows the links on the login screen and
the AI notice before the first Ask Inventory Bot question. Nothing secret is
in the body (only booleans and public URLs/text).
"""

from fastapi import APIRouter, Response

from app.legal import legal_payload

router = APIRouter(prefix="/api/app", tags=["app"])


@router.get("/legal")
def get_legal(response: Response) -> dict:
    response.headers["Cache-Control"] = "public, max-age=300"
    return legal_payload()
