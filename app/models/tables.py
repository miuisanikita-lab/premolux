"""
Baza jadvallari.

Muhim tushuncha — LANE (oqim):
  Har bank+karta juftligi uchun bitta Lane yaratiladi.
  Masalan 9 ta bank tanlansa — 9 ta Lane, hammasi PARALEL ishlaydi.
  Har Lane o'z holatiga ega: getnumber -> logging_in -> got_code ->
  logging_full -> premium_pending -> premium_confirmed / failed.
"""
from __future__ import annotations
import enum
import datetime as dt
from sqlalchemy import (
    String, Integer, BigInteger, ForeignKey, DateTime, Boolean,
    Enum as SAEnum, Text, JSON, func
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.db import Base


# ═════════════════════════════════════════
# FOYDALANUVCHI VA ROLLAR
# ═════════════════════════════════════════
class Role(str, enum.Enum):
    owner = "owner"
    partner = "partner"
    worker = "worker"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[Role] = mapped_column(SAEnum(Role), default=Role.worker)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    price_per_premium: Mapped[int] = mapped_column(Integer, default=0)   # faqat hamkorlar uchun
    share_percent: Mapped[int] = mapped_column(Integer, default=0)

    pin_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    joined_channel: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    people: Mapped[list["Person"]] = relationship(back_populates="owner")
    bots: Mapped[list["NumberBot"]] = relationship(back_populates="owner")


class InviteCode(Base):
    """Bir martalik taklif kodlari — hamkor/ishchi qo'shish uchun."""
    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    kind: Mapped[str] = mapped_column(String(10))            # "partner" | "worker"
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    used_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


# ═════════════════════════════════════════
# DOSTUP HISOB — raqam so'rash uchun kiritiladigan hisob
# ═════════════════════════════════════════
class AccessAccount(Base):
    """
    Har foydalanuvchining "dostup hisobi" — shu orqali raqam
    beruvchi botlarga /GetNumber yuboriladi.

    MUHIM: bitta owner_id uchun bir vaqtda FAQAT BITTA hisob
    connected=True bo'lishi mumkin. Yangi hisob ulanganda eskisi
    avtomatik uziladi (access_service.py da ta'minlanadi) — bu
    turli foydalanuvchilarning hisoblari bir-biriga aralashib
    ketmasligi uchun MUHIM qoida.
    """
    __tablename__ = "access_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    phone: Mapped[str] = mapped_column(String(20))
    session_file: Mapped[str] = mapped_column(String(256))   # Telethon .session fayli nomi
    proxy_id: Mapped[int | None] = mapped_column(ForeignKey("proxies.id"), nullable=True)
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class NumberBot(Base):
    """Foydalanuvchi ulagan 'raqam beruvchi bot' (masalan @xyz_number_bot)."""
    __tablename__ = "number_bots"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    username: Mapped[str] = mapped_column(String(64))         # @ siz saqlanadi
    slot: Mapped[int] = mapped_column(Integer)                 # 1,2,3 — nechinchi slot
    max_logins: Mapped[int] = mapped_column(Integer, default=15)
    active_logins: Mapped[int] = mapped_column(Integer, default=0)
    connected: Mapped[bool] = mapped_column(Boolean, default=False)

    owner: Mapped[User] = relationship(back_populates="bots")


# ═════════════════════════════════════════
# SHAXS / BANK / KARTA
# ═════════════════════════════════════════
class Person(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    owner: Mapped[User] = relationship(back_populates="people")
    cards: Mapped[list["Card"]] = relationship(back_populates="person")


class Card(Base):
    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"))
    bank_id: Mapped[str] = mapped_column(String(32))          # frontend BL ro'yxatidagi id
    number_enc: Mapped[str] = mapped_column(Text)              # shifrlangan karta raqami
    exp: Mapped[str] = mapped_column(String(7))                 # MM/YY
    cvv_enc: Mapped[str] = mapped_column(Text)                  # shifrlangan CVV
    holder_name: Mapped[str] = mapped_column(String(128))

    limit: Mapped[int] = mapped_column(Integer, default=3)
    used: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    person: Mapped[Person] = relationship(back_populates="cards")


# ═════════════════════════════════════════
# PROXY
# ═════════════════════════════════════════
class Proxy(Base):
    __tablename__ = "proxies"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(10), default="socks5")
    host: Mapped[str] = mapped_column(String(128))
    port: Mapped[int] = mapped_column(Integer)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    password_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    in_use: Mapped[bool] = mapped_column(Boolean, default=False)   # bandmi
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


# ═════════════════════════════════════════
# BUYURTMA VA LANE (parallel oqimlar)
# ═════════════════════════════════════════
class OrderStatus(str, enum.Enum):
    running = "running"
    finished = "finished"
    cancelled = "cancelled"


class Order(Base):
    """
    Bitta 'Premium olish' bosilganda yaratiladigan buyurtma.
    Ichida N ta Lane bo'ladi (N = tanlangan bank/karta soni).
    """
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id"))
    status: Mapped[OrderStatus] = mapped_column(SAEnum(OrderStatus), default=OrderStatus.running)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    lanes: Mapped[list["Lane"]] = relationship(back_populates="order")


class LaneStatus(str, enum.Enum):
    """
    Siz tasvirlagan holat mashinasi — aynan shu ketma-ketlikda.
    """
    queued          = "queued"           # navbatda kutmoqda
    getting_number   = "getting_number"    # /GetNumber yuborildi
    logging_in       = "logging_in"        # raqamga Telegram kodi kutilmoqda
    login_failed     = "login_failed"      # noto'g'ri kod/parol -> Freeze bosilgan
    waiting_stuck    = "waiting_stuck"     # "waiting" holatida qotib qoldi -> Freeze
    got_code         = "got_code"          # bot "Code:/Pass:" berdi
    logging_full     = "logging_full"      # Telethon shu Code/Pass bilan to'liq login qilmoqda
    premium_pending  = "premium_pending"   # @PremiumBot da karta kiritilmoqda
    otp_waiting      = "otp_waiting"       # bank OTP so'ramoqda, forwarderdan kutilmoqda
    checking         = "checking"          # "Check premium" bosilgan, javob kutilmoqda
    confirmed        = "confirmed"         # "✅ ... premium activated and counted."
    failed           = "failed"            # boshqa har qanday yakuniy xato


class Lane(Base):
    """Bitta bank + bitta karta = bitta mustaqil, parallel ishlaydigan oqim."""
    __tablename__ = "lanes"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"))
    bot_id: Mapped[int] = mapped_column(ForeignKey("number_bots.id"))
    proxy_id: Mapped[int | None] = mapped_column(ForeignKey("proxies.id"), nullable=True)

    status: Mapped[LaneStatus] = mapped_column(SAEnum(LaneStatus), default=LaneStatus.queued)

    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)   # bot bergan raqam
    login_code: Mapped[str | None] = mapped_column(String(16), nullable=True)      # "Code:" qiymati
    login_pass: Mapped[str | None] = mapped_column(String(64), nullable=True)      # "Pass:" qiymati
    session_file: Mapped[str | None] = mapped_column(String(256), nullable=True)

    otp_code: Mapped[str | None] = mapped_column(String(16), nullable=True)        # forwarderdan kelgan
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    order: Mapped[Order] = relationship(back_populates="lanes")


# ═════════════════════════════════════════
# SMS RELAY — forwarder ilovadan kelgan xabarlar
# ═════════════════════════════════════════
class RelayDevice(Base):
    """Har SMS forwarder ilova o'rnatilgan qurilma — o'z tokeni bilan."""
    __tablename__ = "relay_devices"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_seen_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class RelaySms(Base):
    """Kelgan har bir bank SMS — qaysi Lane kutayotganiga moslashtiriladi."""
    __tablename__ = "relay_sms"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("relay_devices.id"))
    lane_id: Mapped[int | None] = mapped_column(ForeignKey("lanes.id"), nullable=True)

    sender: Mapped[str] = mapped_column(String(64))
    body: Mapped[str] = mapped_column(Text)
    extracted_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    matched: Mapped[bool] = mapped_column(Boolean, default=False)
    received_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


# ═════════════════════════════════════════
# HISOBOT — kunlik statistika (StatsPage uchun)
# ═════════════════════════════════════════
class DailyStat(Base):
    __tablename__ = "daily_stats"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    date: Mapped[dt.date] = mapped_column(DateTime)
    hour: Mapped[int] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer, default=0)


# ═════════════════════════════════════════
# SOZLAMALAR — frontenddagi SettingsPage bilan bir xil maydonlar
# ═════════════════════════════════════════
class UserSettings(Base):
    """
    Frontenddagi cfg obyekti bilan bevosita mos:
    streams, retry, cardCap, pin, lockAfter, maskPan,
    nOk, nLimit, nErr, daily, dailyAt, haptic, calm.
    """
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)

    streams: Mapped[int] = mapped_column(Integer, default=8)       # bir vaqtda nechta Lane
    retry: Mapped[int] = mapped_column(Integer, default=1)
    card_cap: Mapped[int] = mapped_column(Integer, default=3)       # karta limiti (default)

    pin_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    lock_after_days: Mapped[int] = mapped_column(Integer, default=5)
    mask_pan: Mapped[bool] = mapped_column(Boolean, default=True)

    notify_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_limit: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_err: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_daily: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_daily_at: Mapped[int] = mapped_column(Integer, default=21)

    haptic: Mapped[bool] = mapped_column(Boolean, default=True)
    calm: Mapped[bool] = mapped_column(Boolean, default=False)

    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
