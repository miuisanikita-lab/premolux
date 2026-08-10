"""
ORDER SERVICE — "Premium olish" bosilganda ishga tushadi.

Frontenddagi PremiumPage step 4 ("launch") shu yerga to'g'ridan-to'g'ri
bog'lanadi: /orders/start so'rovi kelganda, tanlangan har bank+karta
uchun bitta Lane yaratiladi va parallel ishga tushiriladi — lekin
foydalanuvchi Sozlamalarda belgilagan "streams" chegarasidan oshmaydi.
"""
from __future__ import annotations
import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tables import Order, OrderStatus, Lane, LaneStatus, Card, NumberBot, Proxy, UserSettings
from app.workers.lane_worker import run_lane
from app.services.telethon_pool import TelethonPool

# Butun ilova uchun bitta pool — Telethon mijozlarini qayta ishlatadi
_pool = TelethonPool()


def get_pool() -> TelethonPool:
    """Boshqa modullar (access_router.py va h.k.) shu orqali bitta umumiy pool ni oladi."""
    return _pool


async def start_order(db: AsyncSession, owner_id: int, person_id: int,
                       selections: list[dict]) -> Order:
    """
    selections — frontenddan keladigan ro'yxat:
      [{ "cardId": 12, "bankId": "uzum" }, { "cardId": 15, "bankId": "kapital" }, ...]

    Har biriga bitta bo'sh raqam-beruvchi bot va bo'sh proxy tayinlanadi,
    so'ng barcha Lane'lar bir vaqtda ishga tushiriladi — lekin
    foydalanuvchi Sozlamalarda belgilagan "streams" chegarasidan oshmaydi.
    """
    order = Order(owner_id=owner_id, person_id=person_id, status=OrderStatus.running)
    db.add(order)
    await db.flush()  # order.id kerak bo'ladi

    bots = (await db.execute(
        select(NumberBot).where(NumberBot.owner_id == owner_id, NumberBot.connected == True)
    )).scalars().all()
    if not bots:
        raise ValueError("Ulangan raqam beruvchi bot yo'q")

    free_proxies = (await db.execute(
        select(Proxy).where(Proxy.owner_id == owner_id, Proxy.in_use == False)
    )).scalars().all()

    settings_row = (await db.execute(
        select(UserSettings).where(UserSettings.owner_id == owner_id)
    )).scalars().first()
    max_streams = settings_row.streams if settings_row else 8

    lanes: list[Lane] = []
    for i, sel in enumerate(selections):
        bot = bots[i % len(bots)]                       # botlar orasida navbat bilan taqsimlanadi
        proxy = free_proxies[i] if i < len(free_proxies) else None

        lane = Lane(
            order_id=order.id,
            card_id=sel["cardId"],
            bot_id=bot.id,
            proxy_id=proxy.id if proxy else None,
            status=LaneStatus.queued,
        )
        if proxy:
            proxy.in_use = True
        db.add(lane)
        lanes.append(lane)

    await db.commit()
    for lane in lanes:
        await db.refresh(lane)

    # ── LANE'LAR PARALEL, LEKIN "streams" CHEGARASIDAN OSHMAYDI ──
    asyncio.create_task(_run_all_lanes([l.id for l in lanes], max_streams))

    return order


async def _run_all_lanes(lane_ids: list[int], max_streams: int) -> None:
    """
    asyncio.Semaphore orqali bir vaqtda ishlaydigan Lane sonini
    Sozlamalardagi "Bir vaqtda oqim" qiymati bilan cheklaydi.
    Masalan 9 ta Lane, lekin streams=8 bo'lsa — 8 tasi darrov,
    9-chisi biri tugagach boshlanadi.
    """
    sem = asyncio.Semaphore(max(1, max_streams))

    async def _guarded(lid: int):
        async with sem:
            await run_lane(lid, _pool)

    await asyncio.gather(*[_guarded(lid) for lid in lane_ids], return_exceptions=True)


async def order_summary(db: AsyncSession, order_id: int) -> dict:
    """Frontendga qaytariladigan holat — har Lane holati alohida."""
    lanes = (await db.execute(select(Lane).where(Lane.order_id == order_id))).scalars().all()
    return {
        "orderId": order_id,
        "total": len(lanes),
        "confirmed": sum(1 for l in lanes if l.status == LaneStatus.confirmed),
        "failed": sum(1 for l in lanes if l.status == LaneStatus.failed),
        "lanes": [
            {
                "id": l.id, "status": l.status.value,
                "phoneNumber": l.phone_number, "errorReason": l.error_reason,
            } for l in lanes
        ],
    }
