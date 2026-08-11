"""Har muvaffaqiyatli Lane tugagach — statistikaga +1 (frontenddagi bump() bilan bir xil)."""
import datetime as dt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.tables import DailyStat, Lane, Order


async def bump_today(db: AsyncSession, lane: Lane) -> None:
    order = await db.get(Order, lane.order_id)
    now = dt.datetime.utcnow()
    today = dt.datetime(now.year, now.month, now.day)

    row = (await db.execute(
        select(DailyStat).where(
            DailyStat.owner_id == order.owner_id,
            DailyStat.date == today,
            DailyStat.hour == now.hour,
        )
    )).scalars().first()

    if row:
        row.count += 1
    else:
        db.add(DailyStat(owner_id=order.owner_id, date=today, hour=now.hour, count=1))

    await db.commit()
