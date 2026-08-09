"""
AUTH ROUTER — frontend api.js dagi /auth/verify chaqiruvi shu yerga tushadi.
Ilova ochilganda "salom, men shu Telegram foydalanuvchisiman" deb tekshiradi.
"""
from fastapi import APIRouter, Depends
from app.core.security import get_current_user
from app.models.tables import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/verify")
async def verify(user: User = Depends(get_current_user)):
    return {
        "id": user.id,
        "tgId": user.tg_id,
        "name": user.name,
        "role": user.role.value,
    }
