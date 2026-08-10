"""
ACCESS SERVICE — dostup hisobni ulash/almashtirish.

QOIDA (siz tasdiqlagan): bitta owner uchun bir vaqtda FAQAT BITTA
dostup hisob faol bo'ladi. Yangisi ulanganda — eskisi AVTOMATIK
uziladi (Telethon sessiyasi yopiladi, connected=False qilinadi).

Bu ikki narsani kafolatlaydi:
  1. Hech qachon "qaysi hisob ishlatilyapti" degan chalkashlik bo'lmaydi
  2. Egasi va hamkorning hisoblari ARALASHMAYDI — chunki har biri
     faqat O'Z owner_id siga tegishli AccessAccount bilan ishlaydi
     (barcha so'rovlar shu WHERE owner_id=... orqali filtrlanadi)
"""
from __future__ import annotations
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import AccessAccount
from app.services.telethon_pool import TelethonPool


async def connect_access_account(
    db: AsyncSession, owner_id: int, phone: str, session_file: str,
    pool: TelethonPool,
) -> AccessAccount:
    """
    Yangi dostup hisobni ulaydi. Shu owner ning OLDINGI faol
    hisobi bo'lsa — avtomatik uziladi.
    """
    # ── eskisini uzish ──
    old_accounts = (await db.execute(
        select(AccessAccount).where(
            AccessAccount.owner_id == owner_id,
            AccessAccount.connected == True,
        )
    )).scalars().all()

    for old in old_accounts:
        await pool.disconnect_access_client(old)
        old.connected = False

    # ── yangisini yaratish/faollashtirish ──
    existing = (await db.execute(
        select(AccessAccount).where(
            AccessAccount.owner_id == owner_id,
            AccessAccount.phone == phone,
        )
    )).scalars().first()

    if existing:
        existing.connected = True
        existing.session_file = session_file
        account = existing
    else:
        account = AccessAccount(
            owner_id=owner_id, phone=phone,
            session_file=session_file, connected=True,
        )
        db.add(account)

    await db.commit()
    await db.refresh(account)
    return account


async def get_active_access_account(db: AsyncSession, owner_id: int) -> AccessAccount | None:
    """
    Lane worker shu funksiya orqali dostup hisobni oladi — HAR DOIM
    owner_id bo'yicha filtrlanadi, shuning uchun boshqa foydalanuvchi
    (masalan hamkor) hisobi bilan aralashib ketish MUMKIN EMAS.
    """
    return (await db.execute(
        select(AccessAccount).where(
            AccessAccount.owner_id == owner_id,
            AccessAccount.connected == True,
        )
    )).scalars().first()


async def disconnect_access_account(db: AsyncSession, owner_id: int, pool: TelethonPool) -> None:
    """Operator qo'lda uzmoqchi bo'lsa (masalan Botlar sahifasida "Uzish" bosilsa)."""
    account = await get_active_access_account(db, owner_id)
    if account:
        await pool.disconnect_access_client(account)
        account.connected = False
        await db.commit()
