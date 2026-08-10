"""
DEBUG ROUTER — faqat sinov davrida foydali. Bank sahifasida OTP
maydoni topilmasa, tizim avtomatik surat oladi — buni shu yerdan
yuklab olib, HAQIQATAN sahifada nima borligini ko'rish mumkin.

MUHIM: bu yo'l ATAYIN autentifikatsiyasiz (ochiq) — faqat sinov
davrida, tezda tekshirish uchun. Production'da bu router olib
tashlanishi yoki himoyalanishi kerak.
"""
import os
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/debug", tags=["debug"])


@router.get("/last-screenshot")
async def last_screenshot():
    path = "/tmp/last_bank_page.png"
    if not os.path.exists(path):
        raise HTTPException(404, "Hali hech qanday surat saqlanmagan")
    return FileResponse(path, media_type="image/png")
