"""
PEOPLE ROUTER — frontenddagi Kartalar sahifasi (Shaxs -> Bank -> Karta)
shu yerga ulanadi.

Bu — muhim yetishmayotgan qism edi: frontend POST /people/{id}/cards
ga so'rov yuborardi, lekin backend'da bu yo'l umuman yo'q edi —
kartalar faqat brauzerda saqlanib, order_service HECH QACHON
haqiqiy kartani topolmasdi.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import get_current_user
from app.models.tables import User, Person, Card
from app.services.crypto import encrypt, decrypt

router = APIRouter(tags=["people"])


# ═════════════════════════════════════════
# SHAXSLAR
# ═════════════════════════════════════════
class PersonIn(BaseModel):
    name: str


@router.get("/people")
async def list_people(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    people = (await db.execute(
        select(Person).where(Person.owner_id == user.id)
    )).scalars().all()

    out = []
    for p in people:
        cards = (await db.execute(select(Card).where(Card.person_id == p.id))).scalars().all()
        out.append({
            "id": p.id, "name": p.name,
            "cards": [_card_out(c) for c in cards],
        })
    return out


@router.post("/people")
async def create_person(
    payload: PersonIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    person = Person(owner_id=user.id, name=payload.name)
    db.add(person)
    await db.commit()
    await db.refresh(person)
    return {"id": person.id, "name": person.name, "cards": []}


@router.delete("/people/{person_id}")
async def delete_person(
    person_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    person = await db.get(Person, person_id)
    if not person or person.owner_id != user.id:
        raise HTTPException(404, "Shaxs topilmadi")

    cards = (await db.execute(select(Card).where(Card.person_id == person_id))).scalars().all()
    for c in cards:
        await db.delete(c)
    await db.delete(person)
    await db.commit()
    return {"ok": True}


# ═════════════════════════════════════════
# KARTALAR
# ═════════════════════════════════════════
class CardIn(BaseModel):
    bankId: str
    num: str            # to'liq karta raqami (masalan "8600 1234 5678 9012")
    exp: str             # "MM/YY"
    cvv: str
    name: str = ""       # karta egasining ismi (frontend shu nom bilan yuboradi)
    limit: int = 3


def _card_out(c: Card) -> dict:
    """Karta chiqarilganda raqam NIQOBLANADI — to'liq raqam hech qachon qaytarilmaydi."""
    try:
        full = decrypt(c.number_enc)
        masked = full[:4] + " •••• •••• " + full[-4:] if len(full) >= 8 else "••••"
    except Exception:
        masked = "••••"
    return {
        "id": c.id, "bankId": c.bank_id, "num": masked,
        "exp": c.exp, "name": c.holder_name,
        "limit": c.limit, "used": c.used,
    }


@router.post("/people/{person_id}/cards")
async def add_card(
    person_id: int,
    payload: CardIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    person = await db.get(Person, person_id)
    if not person or person.owner_id != user.id:
        raise HTTPException(404, "Shaxs topilmadi")

    number_clean = payload.num.replace(" ", "")
    card = Card(
        person_id=person_id,
        bank_id=payload.bankId,
        number_enc=encrypt(number_clean),
        exp=payload.exp,
        cvv_enc=encrypt(payload.cvv),
        holder_name=payload.name,
        limit=payload.limit,
    )
    db.add(card)
    await db.commit()
    await db.refresh(card)
    return _card_out(card)


@router.delete("/cards/{card_id}")
async def delete_card(
    card_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    card = await db.get(Card, card_id)
    if not card:
        raise HTTPException(404, "Karta topilmadi")
    person = await db.get(Person, card.person_id)
    if not person or person.owner_id != user.id:
        raise HTTPException(404, "Karta topilmadi")

    await db.delete(card)
    await db.commit()
    return {"ok": True}
