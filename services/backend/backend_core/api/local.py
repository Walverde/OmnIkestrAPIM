from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from .. import runtime


router = APIRouter()


@router.get("/api/local/picker-token")
def local_picker_token():
    if not runtime.PICKER_TOKEN:
        raise HTTPException(status_code=503, detail="Seletor local não configurado.")
    return JSONResponse(
        content={"token": runtime.PICKER_TOKEN},
        headers={"Cache-Control": "no-store"},
    )
