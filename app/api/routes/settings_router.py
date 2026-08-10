"""
SETTINGS ROUTER — frontenddagi SettingsPage bilan bir xil.

GET  /settings — joriy sozlamalarni qaytaradi (yo'q bo'lsa standart yaratiladi)
PUT  /settings — o'zgargan maydonlarni saqlaydi

order_service.py endi shu sozlamalardan foydalanadi:
  - streams -> bir vaqtda nechta Lane ishga tushishi mumkinligini cheklaydi
  - card_cap -> yangi karta qo'shilganda standart limit sifatida ishlatiladi
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import get_current_user
from app.models.tables import User, UserSettings

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsOut(BaseModel):
    streams: int
    retry: int
    cardCap: int
    pin: bool
    lockAfter: int
    maskPan: bool
    nOk: bool
    nLimit: bool
    nErr: bool
    daily: bool
    dailyAt: int
    haptic: bool
    calm: bool

    class Config:
        from_attributes = True


class SettingsIn(BaseModel):
    streams: int | None = None
    retry: int | None = None
    cardCap: int | None = None
    pin: bool | None = None
    lockAfter: int | None = None
    maskPan: bool | None = None
    nOk: bool | None = None
    nLimit: bool | None = None
    nErr: bool | None = None
    daily: bool | None = None
    dailyAt: int | None = None
    haptic: bool | None = None
    calm: bool | None = None


def _to_out(s: UserSettings) -> SettingsOut:
    return SettingsOut(
        streams=s.streams, retry=s.retry, cardCap=s.card_cap,
        pin=s.pin_enabled, lockAfter=s.lock_after_days, maskPan=s.mask_pan,
        nOk=s.notify_ok, nLimit=s.notify_limit, nErr=s.notify_err,
        daily=s.notify_daily, dailyAt=s.notify_daily_at,
        haptic=s.haptic, calm=s.calm,
    )


async def _get_or_create(db: AsyncSession, owner_id: int) -> UserSettings:
    row = (await db.execute(
        select(UserSettings).where(UserSettings.owner_id == owner_id)
    )).scalars().first()
    if not row:
        row = UserSettings(owner_id=owner_id)
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return row


@router.get("", response_model=SettingsOut)
async def get_settings(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    row = await _get_or_create(db, user.id)
    return _to_out(row)


@router.put("", response_model=SettingsOut)
async def update_settings(
    payload: SettingsIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row = await _get_or_create(db, user.id)

    field_map = {
        "streams": "streams", "retry": "retry", "cardCap": "card_cap",
        "pin": "pin_enabled", "lockAfter": "lock_after_days", "maskPan": "mask_pan",
        "nOk": "notify_ok", "nLimit": "notify_limit", "nErr": "notify_err",
        "daily": "notify_daily", "dailyAt": "notify_daily_at",
        "haptic": "haptic", "calm": "calm",
    }
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(row, field_map[key], value)

    await db.commit()
    await db.refresh(row)
    return _to_out(row)
