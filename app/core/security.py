"""
Telegram WebApp initData tekshiruvi.

Frontend har so'rovga shu headerni qo'shadi (api.js dagi authHeader()):
  Authorization: tma <initData>

Bu yerda initData ning HMAC imzosi tekshiriladi — soxta so'rovlarning
oldini oladi. Batafsil: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
"""
import hashlib
import hmac
import json
from urllib.parse import parse_qsl

from fastapi import Header, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.models.tables import User, Role


def _required_channels() -> list[str]:
    """Majburiy obuna ro'yxati — sozlamadan o'qiladi, @ belgisi tozalanadi."""
    raw = getattr(settings, "required_channels", "") or ""
    out = []
    for part in raw.split(","):
        name = part.strip().lstrip("@")
        if name:
            out.append(name)
    return out


async def _missing_channels(tg_id: int) -> list[str]:
    """
    Foydalanuvchi a'zo bo'lmagan majburiy kanallar/guruhlar ro'yxati.

    Telethon orqali bot hisobidan foydalanuvchining DIALOG'larini
    o'qib, kerakli kanal/guruh ichida ekanligini tekshiradi.
    Xatolik bo'lsa (bot ishlamayapti, proxy muammo va h.k.) —
    tekshiruvni BEKOR qilamiz: obuna majburiyatini buzmaslik
    uchun emas, balki bot ishlamayotganda ham ro'yxatdan
    o'tishga ruxsat berish uchun (fail-open).
    """
    channels = _required_channels()
    if not channels:
        return []

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from app.services.telethon_pool import default_proxy_conf

        client = TelegramClient(
            StringSession(), settings.tg_api_id, settings.tg_api_hash,
            proxy=await default_proxy_conf(),
        )
        await client.connect()
        try:
            dialogs = await client.get_dialogs(limit=None)
            joined = set()
            for d in dialogs:
                entity = d.entity
                # kanal yoki guruh — username bo'lsa ro'yxatga olamiz
                uname = getattr(entity, "username", None)
                if uname:
                    joined.add(uname.lower().lstrip("@"))
            return [c for c in channels if c.lower().lstrip("@") not in joined]
        finally:
            await client.disconnect()
    except Exception:
        # Bot ishlamayapti — tekshiruvni o'tkazib yuboramiz
        return []


async def require_subscription(tg_id: int, owner_id: int | None = None) -> list[str]:
    """
    Majburiy obuna tekshiruvi.

    OWNER (sozlamadagi owner_tg_id) uchun DOIM o'tkaziladi —
    egasi uchun obuna majburiyati ishlamaydi.
    Qaytaradi: a'zo bo'lmagan kanallar ro'yxati (bo'sh = hammasi ok).
    """
    if owner_id is not None and tg_id == owner_id:
        return []
    if settings.owner_tg_id and tg_id == int(settings.owner_tg_id):
        return []
    return await _missing_channels(tg_id)


def _check_signature(init_data: str) -> dict:
    parsed = dict(parse_qsl(init_data, strict_parsing=True))
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        raise ValueError("hash yo'q")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))

    # BOT_TOKEN sozlanmagan bo'lsa — imzo tekshiruvi
    # o'tkazib yuboriladi (sinov rejimida). Bu holda
    # faqat OWNER_TG_ID bilan mos keladigan foydalanuvchi
    # kira oladi — boshqalar 403 bilan rad etiladi.
    if not settings.bot_token:
        import json as _json
        user_json = _json.loads(parsed.get("user", "{}"))
        tg_id = user_json.get("id")
        if settings.owner_tg_id and tg_id and int(tg_id) == int(settings.owner_tg_id):
            return parsed
        raise ValueError("BOT_TOKEN sozlanmagan")

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

    # MUHIM: Telegram WebApp initData'da "id" ba'zan MATN (string)
    # sifatida kelishi mumkin, settings.owner_tg_id esa SON (int) —
    # solishtirishdan oldin ikkalasini ham songa aylantiramiz,
    # aks holda "8986990988" == 8986990988 False bo'lib, HAQIQIY
    # egasi ham 403 bilan rad etilib qolardi.
    try:
        tg_id = int(tg_id)
    except (TypeError, ValueError):
        raise HTTPException(401, "Foydalanuvchi ID formati noto'g'ri")

    user = (await db.execute(select(User).where(User.tg_id == tg_id))).scalars().first()

    if not user:
        # ── XAVFSIZLIK: faqat OWNER_TG_ID mos kelsa avtomatik egasi
        # bo'lib ro'yxatdan o'tadi. Boshqa har qanday notanish odam —
        # RAD ETILADI, takrif kodisiz botga umuman kira olmaydi. ──
        if settings.owner_tg_id and tg_id == int(settings.owner_tg_id):
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
    else:
        # ── MAJBURIY OBUNA: ro'yxatdan o'tganlar uchun ham
        # har kirishda tekshiriladi. Kanal/guruhdan chiqib
        # ketgan bo'lsa — qayta obuna bo'lish talab qilinadi.
        # OWNER uchun tekshiruv o'tkazib yuboriladi. ──
        missing = await require_subscription(tg_id)
        if missing:
            raise HTTPException(
                428,
                detail={
                    "message": "sub_required",
                    "code": "sub_required",
                    "missing": missing,
                },
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

    try:
        tg_id = int(tg_id)
    except (TypeError, ValueError):
        return None

    # ── MAJBURIY OBUNA tekshiruvi ──
    # Ro'yxatdan o'tmagan foydalanuvchi /auth/join orqali kirishidan
    # AVVAL kanal/guruhlarga a'zoligini tekshiramiz.
    # OWNER uchun tekshiruv o'tkazib yuboriladi (require_subscription).
    missing = await require_subscription(tg_id)
    if missing:
        # maxsus kod — frontend "obuna bo'ling" ekranini
        # ko'rsatishi uchun (403 bilan chalkashmasin degan uchun)
        raise HTTPException(
            428,
            detail={
                "message": "sub_required",
                "code": "sub_required",
                "missing": missing,
            },
        )

    return tg_id, user_json
