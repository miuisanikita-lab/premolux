"""
ACCESS ROUTER — frontenddagi Botlar sahifasidagi "Dostup hisob" bilan bog'lanadi.

Frontend LoginFlow: /auth/send-code -> /auth/verify-code -> /auth/verify-2fa
Muvaffaqiyatli login tugagach — shu yerdagi /access/connect chaqiriladi,
ESKI dostup hisob (agar bo'lsa) AVTOMATIK uziladi.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import get_current_user
from app.models.tables import User
from app.services import access_service
from app.services.order_service import get_pool

router = APIRouter(prefix="/access", tags=["access"])


class ConnectIn(BaseModel):
    phone: str
    sessionFile: str   # Telethon login tugagach yaratilgan sessiya fayli nomi


@router.post("/connect")
async def connect(
    payload: ConnectIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Yangi dostup hisob ulanadi. Shu foydalanuvchining OLDINGI hisobi
    bo'lsa — avtomatik uziladi (access_service.py da kafolatlangan).
    """
    account = await access_service.connect_access_account(
        db, owner_id=user.id, phone=payload.phone,
        session_file=payload.sessionFile, pool=get_pool(),
    )
    return {"connected": True, "phone": account.phone}


@router.get("/status")
async def status(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    account = await access_service.get_active_access_account(db, user.id)
    if not account:
        return {"connected": False}
    return {"connected": True, "phone": account.phone}


@router.post("/disconnect")
async def disconnect(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await access_service.disconnect_access_account(db, user.id, get_pool())
    return {"connected": False}
