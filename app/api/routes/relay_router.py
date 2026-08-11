"""
RELAY ROUTER — SMS forwarder Android ilovasi shu yerga POST qiladi.

Android ilova (RelayApi.kt) POST /relay/sms ga:
  { token, device, sender, body, code, received_at }

Bu yerda vazifa: kelgan SMS qaysi Lane "otp_waiting" holatida kutayotgan
bo'lsa, o'sha Lane ga OTP ni yozib qo'yish. lane_worker.py dagi
_wait_for_otp() shu maydonni kuzatib turadi.

Moslashtirish qoidasi (eng oddiy versiya — keyin aniqlashtiriladi):
  Bitta owner uchun bir vaqtda faqat bitta Lane "otp_waiting" holatida
  bo'lishi shart emas (9 tasi parallel!). Shuning uchun MUHIM: qaysi
  Lane qaysi SMS ni kutayotgani ANIQ bilinishi kerak. Buning uchun ikki
  yondashuv bor:
    A) Har Lane uchun vaqtinchalik virtual raqam beriladi, o'sha raqam
       device tokeni bilan bog'lanadi (1 qurilma = 1 lane bir vaqtda)
    B) SMS matnidan summa/vaqt orqali eng yaqin "otp_waiting" Lane
       bilan moslashtiriladi (aniqlik pastroq)

  Hozircha (A) asosida: RelayDevice bitta owner ga tegishli, va owner
  ning "otp_waiting" holatidagi ENG ESKI Lane siga kod beriladi —
  FIFO tartibda. Bank sahifalari sinovdan o'tgach, aniqroq bog'lash
  (masalan karta oxirgi 4 raqami orqali) qo'shiladi.
"""
from __future__ import annotations
import datetime as dt
from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import get_current_user
from app.models.tables import RelayDevice, RelaySms, Lane, LaneStatus, User
from app.services.sms_filter import extract_code

router = APIRouter(prefix="/relay", tags=["relay"])


class RegisterDeviceIn(BaseModel):
    token: str
    deviceModel: str | None = None


@router.post("/register")
async def register_device(
    payload: RegisterDeviceIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    MUHIM: bu — avval YETISHMAGAN, lekin ZARUR bo'lgan qism edi.
    Android ilova o'zining tokenini yaratadi, lekin bu token
    bazada HECH QACHON saqlanmagan bo'lsa, /relay/sms doim
    "noma'lum qurilma" (401) deb rad etardi. Endi operator
    WebApp orqali shu tokenni O'Z HISOBIGA BOG'LAYDI.
    """
    existing = (await db.execute(
        select(RelayDevice).where(RelayDevice.token == payload.token)
    )).scalars().first()

    if existing:
        if existing.owner_id != user.id:
            raise HTTPException(400, "Bu token boshqa hisobga bog'langan")
        existing.device_model = payload.deviceModel
        await db.commit()
        return {"ok": True, "registered": True}

    device = RelayDevice(
        owner_id=user.id, token=payload.token, device_model=payload.deviceModel,
    )
    db.add(device)
    await db.commit()
    return {"ok": True, "registered": True}


@router.get("/status")
async def relay_status(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    device = (await db.execute(
        select(RelayDevice).where(RelayDevice.owner_id == user.id)
    )).scalars().first()
    if not device:
        return {"connected": False}
    return {
        "connected": True,
        "deviceModel": device.device_model,
        "lastSeenAt": device.last_seen_at.isoformat() if device.last_seen_at else None,
    }



class SmsIn(BaseModel):
    token: str
    device: str | None = None
    sender: str
    body: str
    code: str | None = None
    received_at: int | None = None


@router.post("/sms")
async def receive_sms(payload: SmsIn, db: AsyncSession = Depends(get_db)):
    device = (await db.execute(
        select(RelayDevice).where(RelayDevice.token == payload.token)
    )).scalars().first()

    if not device:
        # noma'lum token — jim rad etamiz, xato sababini oshkor qilmaymiz
        raise HTTPException(status_code=401, detail="Noma'lum qurilma")

    device.last_seen_at = dt.datetime.utcnow()

    code = payload.code or extract_code(payload.body)

    sms = RelaySms(
        device_id=device.id,
        sender=payload.sender,
        body=payload.body,
        extracted_code=code,
        matched=False,
    )
    db.add(sms)

    # ── FIFO: shu owner ning eng eski "otp_waiting" Lane siga yetkazamiz ──
    lane = (await db.execute(
        select(Lane)
        .join(Lane.order)
        .where(Lane.status == LaneStatus.otp_waiting)
        .order_by(Lane.started_at.asc())
        .limit(1)
    )).scalars().first()

    if lane and code:
        lane.otp_code = code
        sms.lane_id = lane.id
        sms.matched = True

    await db.commit()
    return {"ok": True, "matched": sms.matched}


@router.get("/ping")
async def ping(authorization: str | None = Header(None), db: AsyncSession = Depends(get_db)):
    token = (authorization or "").replace("Bearer ", "").strip()
    device = (await db.execute(
        select(RelayDevice).where(RelayDevice.token == token)
    )).scalars().first()
    if not device:
        raise HTTPException(status_code=401, detail="Noma'lum qurilma")
    return {"ok": True}
