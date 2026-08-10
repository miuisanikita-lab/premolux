"""
ORDERS ROUTER — frontenddagi PremiumPage.launch() shu yerga POST qiladi.

Eslatma: frontend api.js da yozilgan:
  await api.post("/orders/start", {
    personId: who?.id,
    banks: chosen.map(g=>({ bankId:g.id, cardId: picks[g.id] })),
  });

Shu formatga aynan mos.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import get_current_user
from app.services import order_service
from app.models.tables import User

router = APIRouter(prefix="/orders", tags=["orders"])


class BankSelection(BaseModel):
    bankId: str
    cardId: int


class StartOrderIn(BaseModel):
    personId: int
    banks: list[BankSelection]


@router.post("/start")
async def start_order(
    payload: StartOrderIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not payload.banks:
        raise HTTPException(400, "Kamida bitta bank tanlanishi kerak")

    order = await order_service.start_order(
        db, owner_id=user.id, person_id=payload.personId,
        selections=[b.model_dump() for b in payload.banks],
    )
    return {"orderId": order.id, "laneCount": len(payload.banks)}


@router.get("/{order_id}")
async def get_order(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Frontend jonli holatni shu yerdan so'rab turadi (polling yoki WebSocket)."""
    return await order_service.order_summary(db, order_id)
