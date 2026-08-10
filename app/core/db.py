"""SQLAlchemy async ulanish va sessiya boshqaruvi."""
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool
from app.core.config import settings

# SQLite fayl-asosli baza — fon vazifasi (Lane worker) va oddiy
# so'rovlar BIR VAQTDA turli ulanish ochsa, "no active connection"
# kabi xatolar chiqishi mumkin. StaticPool + check_same_thread=False
# barcha so'rovlarni BITTA, doimiy ulanish orqali o'tkazadi — bu
# SQLite uchun eng barqaror usul (PostgreSQL'da bu shart emas).
_is_sqlite = settings.database_url.startswith("sqlite")

_engine_kwargs = {"echo": False, "pool_pre_ping": True}
if _is_sqlite:
    _engine_kwargs["poolclass"] = StaticPool
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_async_engine(settings.database_url, **_engine_kwargs)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db():
    """FastAPI dependency — har so'rovga alohida sessiya beradi."""
    async with SessionLocal() as session:
        yield session
