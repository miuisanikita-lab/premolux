"""
BOTS ROUTER — frontenddagi Botlar sahifasi (raqam beruvchi botlar
slotlari) shu yerga ulanadi.

Frontend BotRow.conn() hozircha faqat mahalliy holatni o'zgartirar
edi — bu MUHIM XATO edi, chunki backend bu haqda hech narsa bilmasdi.
Endi bot ulanganda /bots/connect chaqiriladi, backend buni
NumberBot jadvaliga yozadi — order_service shu yerdan o'qiydi.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import get_current_user
from app.models.tables import User, NumberBot

router = APIRouter(prefix="/bots", tags=["bots"])


class ConnectBotIn(BaseModel):
    slot: int              # 1, 2 yoki 3
    username: str          # "@" bilan yoki bilan ham bo'lishi mumkin
    maxLogins: int = 15


@router.get("")
async def list_bots(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    bots = (await db.execute(
        select(NumberBot).where(NumberBot.owner_id == user.id).order_by(NumberBot.slot)
    )).scalars().all()
    return [
        {
            "id": b.id, "slot": b.slot, "username": b.username,
            "connected": b.connected, "maxLogins": b.max_logins,
            "activeLogins": b.active_logins,
        } for b in bots
    ]


@router.post("/connect")
async def connect_bot(
    payload: ConnectBotIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    username = payload.username.lstrip("@").strip()
    if not username:
        raise HTTPException(400, "Bot username kiritilmagan")

    existing = (await db.execute(
        select(NumberBot).where(NumberBot.owner_id == user.id, NumberBot.slot == payload.slot)
    )).scalars().first()

    if existing:
        existing.username = username
        existing.connected = True
        existing.max_logins = payload.maxLogins
        bot = existing
    else:
        bot = NumberBot(
            owner_id=user.id, slot=payload.slot, username=username,
            connected=True, max_logins=payload.maxLogins,
        )
        db.add(bot)

    await db.commit()
    await db.refresh(bot)
    return {"id": bot.id, "connected": True, "username": bot.username}


@router.post("/{slot}/disconnect")
async def disconnect_bot(
    slot: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    bot = (await db.execute(
        select(NumberBot).where(NumberBot.owner_id == user.id, NumberBot.slot == slot)
    )).scalars().first()
    if bot:
        bot.connected = False
        bot.username = ""
        await db.commit()
    return {"connected": False}
