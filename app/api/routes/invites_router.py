"""
INVITES ROUTER — owner taklif kodlarini YARATADI.

Nima uchun kerak:
  /auth/join  bazadagi `invite_codes` jadvalidan kodni tekshiradi va
  `used=True` qilib BIR MARTALIKlikni ta'minlaydi. Lekin backendda
  kod YARATISH yo'li umuman yo'q edi — frontend esa kodni faqat
  localStorage'da yaratardi. Natijada: owner brauzerida kod ko'rardi,
  lekin boshqa odam shu kodni kiritganda /auth/join "Kod noto'g'ri"
  deb rad etardi.

Bu endpoint owner'ga serverda haqiqiy kod yaratish imkonini beradi.
"""
import secrets
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.security import get_current_user
from app.models.tables import InviteCode, Role, User

router = APIRouter(tags=["auth"])


class InviteCreateIn(BaseModel):
    kind: str = "worker"          # "worker" | "partner"
    count: int = 1                # birdaniga bir nechta


def _is_owner(user: User) -> bool:
    """Faqat egasi kod yarata oladi."""
    if user.role == Role.owner:
        return True
    if settings.owner_tg_id and int(user.tg_id) == int(settings.owner_tg_id):
        return True
    return False


def _make_code(kind: str = "worker") -> str:
    """
    Kodni CHIZIQCHASIZ saqlaydi: "PLXAB12CD34EF56" (15 belgi).
    Prefiks: PLX — hamkor, PLW — ishchi.
    """
    pre = "PLX" if kind == "partner" else "PLW"
    return pre + secrets.token_hex(6).upper()


@router.post("/invites")
async def create_invites(
    payload: InviteCreateIn,
    db: AsyncSession = Depends(get_db),
    owner: User = Depends(get_current_user),
):
    """
    Owner uchun bir yoki bir nechta taklif kodi yaratadi.
    """
    if not _is_owner(owner):
        raise HTTPException(403, "Kod yaratish faqat egaga ochiq")

    kind = (payload.kind or "worker").lower()
    if kind not in ("worker", "partner"):
        raise HTTPException(422, "kind faqat 'worker' yoki 'partner' bo'lishi kerak")

    try:
        count = max(1, min(int(payload.count), 20))
    except (TypeError, ValueError):
        count = 1

    made = []
    for _ in range(count):
        code = _make_code(kind)
        for _try in range(5):
            clash = (await db.execute(
                select(InviteCode).where(InviteCode.code == code)
            )).scalars().first()
            if not clash:
                break
            code = _make_code(kind)

        db.add(InviteCode(code=code, kind=kind, created_by=owner.id, used=False))
        made.append(code)

    await db.commit()
    return {"ok": True, "kind": kind, "codes": made}
