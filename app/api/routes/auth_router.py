"""
AUTH ROUTER

Ikki xil narsani bajaradi:

1. /auth/verify — frontend har ochilishda "salom, men shu Telegram
   foydalanuvchisiman" deb tekshiradi (initData imzosi orqali)

2. /auth/send-code, /auth/verify-code, /auth/verify-2fa —
   DOSTUP HISOBNI ulash uchun Telethon login oqimi. Bu — sizning
   operatoringiz o'z Telegram hisobiga kirib, uni "dostup hisob"
   sifatida belgilashi (frontenddagi Botlar sahifasidagi LoginFlow
   shu uch bosqichdan foydalanadi).
"""
import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PhoneNumberInvalidError,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.security import get_current_user, get_user_or_none
from app.models.tables import User, InviteCode, Role
from sqlalchemy import select
from app.services import access_service
from app.services.order_service import get_pool
from app.services.telethon_pool import default_proxy_conf

router = APIRouter(prefix="/auth", tags=["auth"])

# Login jarayoni bir necha bosqichdan iborat (raqam -> kod -> parol),
# shuning uchun oraliq Telethon mijozini shu yerda vaqtincha saqlaymiz —
# har foydalanuvchi (owner_id) uchun alohida.
_pending_clients: dict[int, dict] = {}


@router.post("/verify")
async def verify(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "tgId": user.tg_id,
        "name": user.name,
        "role": user.role.value,
    }


class JoinIn(BaseModel):
    code: str


@router.post("/join")
async def join(
    payload: JoinIn,
    db: AsyncSession = Depends(get_db),
    who=Depends(get_user_or_none),
):
    """
    Ro'yxatdan o'tmagan odam FAQAT shu yo'l orqali kirishi mumkin —
    to'g'ri, ishlatilmagan taklif kodi bilan. Frontenddagi Onboarding
    shu yerga so'rov yuboradi.
    """
    if not who:
        raise HTTPException(401, "Telegram orqali ochilishi kerak")
    tg_id, user_json = who

    invite = (await db.execute(
        select(InviteCode).where(InviteCode.code == payload.code, InviteCode.used == False)
    )).scalars().first()
    if not invite:
        raise HTTPException(400, "Kod noto'g'ri yoki allaqachon ishlatilgan")

    existing = (await db.execute(select(User).where(User.tg_id == tg_id))).scalars().first()
    if existing:
        raise HTTPException(400, "Siz allaqachon ro'yxatdan o'tgansiz")

    role = Role.partner if invite.kind == "partner" else Role.worker
    new_user = User(
        tg_id=tg_id,
        name=f"{user_json.get('first_name','')} {user_json.get('last_name','')}".strip(),
        username=user_json.get("username"),
        role=role,
        parent_id=invite.created_by,
    )
    db.add(new_user)

    invite.used = True
    await db.flush()
    invite.used_by_id = new_user.id

    await db.commit()
    await db.refresh(new_user)

    return {"id": new_user.id, "tgId": new_user.tg_id, "role": new_user.role.value}


class SendCodeIn(BaseModel):
    phone: str


@router.post("/send-code")
async def send_code(
    payload: SendCodeIn,
    user: User = Depends(get_current_user),
):
    """
    1-bosqich: raqamga Telegram tasdiqlash kodini yuboradi.
    Frontenddagi LoginFlow.send() shu yerga so'rov jo'natadi.
    """
    os.makedirs(settings.sessions_dir, exist_ok=True)
    session_file = f"access_{user.id}_{payload.phone.lstrip('+')}"
    session_path = os.path.join(settings.sessions_dir, session_file)

    client = TelegramClient(session_path, settings.tg_api_id, settings.tg_api_hash,
                              proxy=await default_proxy_conf())
    await client.connect()

    try:
        sent = await client.send_code_request(payload.phone)
    except PhoneNumberInvalidError:
        await client.disconnect()
        raise HTTPException(400, "Telefon raqami noto'g'ri")
    except Exception as e:
        await client.disconnect()
        raise HTTPException(500, f"Kod yuborilmadi: {e}")

    _pending_clients[user.id] = {
        "client": client,
        "phone": payload.phone,
        "phone_code_hash": sent.phone_code_hash,
        "session_file": session_file,
    }
    return {"ok": True}


class VerifyCodeIn(BaseModel):
    phone: str
    code: str


@router.post("/verify-code")
async def verify_code(
    payload: VerifyCodeIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    2-bosqich: Telegram yuborgan kodni tekshiradi.
    Agar hisobda 2FA (bulut paroli) yoqilgan bo'lsa — 'needPassword'
    qaytaradi, frontend shunda parol so'rash bosqichiga o'tadi.
    """
    pending = _pending_clients.get(user.id)
    if not pending or pending["phone"] != payload.phone:
        raise HTTPException(400, "Avval kod so'rang (/auth/send-code)")

    client: TelegramClient = pending["client"]

    try:
        await client.sign_in(
            phone=payload.phone,
            code=payload.code,
            phone_code_hash=pending["phone_code_hash"],
        )
    except SessionPasswordNeededError:
        return {"ok": True, "needPassword": True}
    except (PhoneCodeInvalidError, PhoneCodeExpiredError):
        raise HTTPException(400, "Kod noto'g'ri yoki eskirgan")
    except Exception as e:
        raise HTTPException(500, f"Xatolik: {e}")

    # 2FA kerak bo'lmadi — darrov ulanadi
    await access_service.connect_access_account(
        db, owner_id=user.id, phone=payload.phone,
        session_file=pending["session_file"], pool=get_pool(),
    )
    # MUHIM: shu vaqtinchalik ulanishni YOPAMIZ — aks holda sessiya
    # fayli "band" bo'lib qoladi, keyinroq Lane worker o'sha faylga
    # kirishga urinsa, JAVOBSIZ kutib qolishi mumkin (SQLite qulfi).
    await client.disconnect()
    _pending_clients.pop(user.id, None)
    return {"ok": True, "needPassword": False}


class Verify2FAIn(BaseModel):
    phone: str
    pass_: str

    class Config:
        fields = {"pass_": "pass"}


@router.post("/verify-2fa")
async def verify_2fa(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """3-bosqich: faqat 2FA yoqilgan hisoblar uchun — bulut parolini tekshiradi."""
    pending = _pending_clients.get(user.id)
    if not pending:
        raise HTTPException(400, "Avval kod tasdiqlang")

    client: TelegramClient = pending["client"]
    password = payload.get("pass") or payload.get("password")

    try:
        await client.sign_in(password=password)
    except Exception:
        raise HTTPException(400, "Parol noto'g'ri")

    await access_service.connect_access_account(
        db, owner_id=user.id, phone=pending["phone"],
        session_file=pending["session_file"], pool=get_pool(),
    )
    # Xuddi shu sabab — vaqtinchalik ulanishni yopamiz
    await client.disconnect()
    _pending_clients.pop(user.id, None)
    return {"ok": True}
