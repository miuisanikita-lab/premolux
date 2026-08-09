"""
PremoLux backend — asosiy kirish nuqtasi.

Ishga tushirish:
  uvicorn app.main:app --reload --port 8000
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.api.routes import auth_router, orders_router, relay_router, ws_router, settings_router, access_router, bots_router
from app.core.db import engine, Base
from app.models import tables  # noqa — barcha jadvallarni ro'yxatdan o'tkazadi


@asynccontextmanager
async def lifespan(app: FastAPI):
    # SQLite/tez sinov uchun: jadvallar avtomatik yaratiladi.
    # Haqiqiy PostgreSQL serverida buning o'rniga Alembic migratsiyasi
    # ishlatiladi (alembic upgrade head) — production da shu qatorni
    # o'chirib, migratsiyaga o'tish tavsiya etiladi.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="PremoLux API", version="1.0.0", lifespan=lifespan)

# Telegram WebApp turli manzillardan ochilishi mumkin — CORS ochiq
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router)
app.include_router(orders_router.router)
app.include_router(relay_router.router)
app.include_router(ws_router.router)
app.include_router(settings_router.router)
app.include_router(access_router.router)
app.include_router(bots_router.router)


@app.get("/")
async def root():
    return {"service": "PremoLux API", "status": "ok"}
