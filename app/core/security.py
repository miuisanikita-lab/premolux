"""
Telegram WebApp initData tekshiruvi.

Frontend har so'rovga shu headerni qo'shadi (api.js dagi authHeader()):
  Authorization: tma <initData>

Bu yerda initData ning HMAC imzosi tekshiriladi — soxta so'rovlarning
oldini oladi. Batafsil: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
import hashlib
import hmac
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.models.tables import User, Role


def _check_signature(init_data: str) -> dict:
    parsed = dict(parse_qsl(init_data, strict_parsing=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise ValueError("hash yo'q")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if calculated_hash != received_hash:
        raise ValueError("imzo mos kelmadi")

    return parsed


async def get_current_user(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("tma "):
        raise HTTPException(401, "Autentifikatsiya kerak")

    init_data = authorization.removeprefix("tma ").strip()

    try:
        parsed = _check_signature(init_data)
    except ValueError:
        raise HTTPException(401, "Noto'g'ri Telegram imzosi")

    import json
    user_json = json.loads(parsed.get("user", "{}"))
    tg_id = user_json.get("id")
    if not tg_id:
        raise HTTPException(401, "Foydalanuvchi ID topilmadi")

    user = (await db.execute(select(User).where(User.tg_id == tg_id))).scalars().first()

    if not user:
        # ── XAVFSIZLIK: faqat OWNER_TG_ID mos kelsa avtomatik egasi
        # bo'lib ro'yxatdan o'tadi. Boshqa har qanday notanish odam —
        # RAD ETILADI, taklif kodisiz botga umuman kira olmaydi. ──
        if settings.owner_tg_id and tg_id == settings.owner_tg_id:
            user = User(
                tg_id=tg_id,
                name=f"{user_json.get('first_name','')} {user_json.get('last_name','')}".strip(),
                username=user_json.get("username"),
                role=Role.owner,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
        else:
            raise HTTPException(
                403,
                "Bu botga faqat taklif kodi orqali qo'shilish mumkin. "
                "Egasi sizga kod berishi kerak.",
            )

    return user


async def get_user_or_none(
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """
    get_current_user bilan bir xil, lekin ro'yxatdan o'tmagan
    (403) foydalanuvchini xato bermay, shunchaki None qaytaradi —
    /auth/join kabi "hali a'zo bo'lmagan odam kirishi mumkin bo'lgan"
    yagona endpoint uchun ishlatiladi.
    """
    if not authorization or not authorization.startswith("tma "):
        return None
    init_data = authorization.removeprefix("tma ").strip()
    try:
        parsed = _check_signature(init_data)
    except ValueError:
        return None

    import json
    user_json = json.loads(parsed.get("user", "{}"))
    tg_id = user_json.get("id")
    if not tg_id:
        return None

    return tg_id, user_json
