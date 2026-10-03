"""
DEBUG ROUTER — faqat sinov davrida foydali. Bank sahifasida OTP
maydoni topilmasa, tizim avtomatik surat oladi — buni shu yerdan
yuklab olib, HAQIQATAN sahifada nima borligini ko'rish mumkin.

MUHIM: bu yo'l ATAYIN autentifikatsiyasiz (ochiq) — faqat sinov
davrida, tezda tekshirish uchun. Production'da bu router olib
tashlanishi yoki himoyalanishi kerak.
"""
import os
from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import FileResponse

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/last-screenshot")
async def last_screenshot():
    path = "/tmp/last_bank_page.png"
    if not os.path.exists(path):
        raise HTTPException(404, "Hali hech qanday surat saqlanmagan")
    return FileResponse(path, media_type="image/png")


@router.get("/auth-info")
async def auth_info(authorization: str | None = Header(None)):
    """
    initData tekshiruvi — nima noto'g'ri ekanini ko'rish uchun.
    Frontend har so'rovga "Authorization: tma <initData>" qo'shadi.
    Bu endpoint qaytaradi:
      - initData qabul qilindi yoki yo'q
      - imzo to'g'ri mi
      - foydalanuvchi kim
      - obuna holati
    """
    from app.core.config import settings
    from app.core.security import _check_signature, _required_channels

    result = {
        "hasAuth": bool(authorization),
        "prefix": authorization[:10] if authorization else None,
        "botTokenSet": bool(settings.bot_token),
        "requiredChannels": _required_channels(),
        "ownerTgId": settings.owner_tg_id,
    }

    if not authorization or not authorization.startswith("tma "):
        result["error"] = "Authorization header yo'q yoki 'tma ' prefiksi yo'q"
        return result

    init_data = authorization.removeprefix("tma ").strip()
    result["initDataLength"] = len(init_data)

    try:
        parsed = _check_signature(init_data)
        result["signatureOk"] = True
        import json
        user_json = json.loads(parsed.get("user", "{}"))
        result["tgId"] = user_json.get("id")
        result["firstName"] = user_json.get("first_name")
        result["username"] = user_json.get("username")
    except ValueError as e:
        result["signatureOk"] = False
        result["error"] = str(e)
    except Exception as e:
        result["signatureOk"] = False
        result["error"] = f"{type(e).__name__}: {e}"

    return result
