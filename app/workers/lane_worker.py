"""
LANE WORKER — bitta bank+karta uchun to'liq avtomatik oqim.

Bu fayl siz aytgan ketma-ketlikni AYNAN bajaradi (haqiqiy bot
xabar formatlariga moslashtirilgan — 2026-08-08 sinovi asosida):

  1. Dostup hisob -> ulangan raqam-beruvchi botga /GetNumber
     Bot javobi: "📞 Your number:\n\n905063984476" + 3 tugma
     (Cancel, Freeze, Get code)

  2. "Get code" bosiladi. Bot NATIJANI o'zi tekshirib, xabarni
     tahrirlaydi:
       muvaffaqiyat -> "📩 Code received:\n📞 Number: ...\n
                        🔐 Code: 40797\n🔑 Pass: 3135915"
                        + 4 tugma (Get code again, Check premium,
                        Freeze, Cancel)
       xato         -> "❌ Error: Login Code NotFound\n📞 Number: ..."
                        -> DARROV Freeze bosiladi, Lane FAILED

  3. Muvaffaqiyat bo'lsa — Code/Pass bilan biz o'zimiz (Telethon)
     shu hisobga TO'LIQ, mustaqil login qilamiz (yangi sessiya)

  4. @PremiumBot ga kirib, tayinlangan kartani kiritamiz

  5. Bank OTP so'rasa -> Lane "otp_waiting" holatiga o'tadi,
     forwarder ilovadan kelgan kodni kutadi

  6. To'lov tugagach -> "Check premium" bosiladi
  7. Javobda "premium activated and counted" bo'lsagina
     -> LANE CONFIRMED, statistikaga +1

Bir nechta Lane bir vaqtda, bir-biridan mustaqil ishlaydi —
asyncio.gather orqali parallel ishga tushiriladi (order_service da).
"""
from __future__ import annotations
import asyncio
import re
import datetime as dt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.core.config import settings
from app.models.tables import Lane, LaneStatus, Card, NumberBot, AccessAccount, Order
from app.services.telethon_pool import TelethonPool
from app.services.crypto import decrypt
from app.services.access_service import get_active_access_account
from app.services import stats_service

# ── haqiqiy bot xabarlariga moslashtirilgan naqshlar ──
CONFIRM_PATTERN = re.compile(r"premium activated and counted", re.I)
ERROR_PATTERN   = re.compile(r"❌\s*Error", re.I)
CODE_PATTERN    = re.compile(r"Code:\s*(\S+)", re.I)
PASS_PATTERN    = re.compile(r"Pass:\s*(\S+)", re.I)
PHONE_PATTERN   = re.compile(r"number:\s*\n*\s*(\+?\d{9,15})", re.I)


class LaneError(Exception):
    """Lane ichida sodir bo'lgan, oqimni to'xtatadigan xato."""
    def __init__(self, reason: str, status: LaneStatus = LaneStatus.failed):
        self.reason = reason
        self.status = status
        super().__init__(reason)


async def run_lane(lane_id: int, pool: TelethonPool) -> None:
    """
    Bitta Lane uchun to'liq oqimni bajaradi. Xato chiqsa ushlab,
    Lane holatini 'failed' qilib, funksiyadan tinch chiqadi —
    boshqa Lane'larga ta'sir qilmasligi uchun.

    MUHIM: login bosqichida xato (login_failed/waiting_stuck)
    chiqsa — Freeze bosilib, DARROV YANGI RAQAM bilan qayta
    urinib ko'riladi (siz belgilagan qoida). Bu — boshqa xato
    turlariga (masalan bot ulanmagan) taalluqli EMAS, ular
    darrov Lane'ni to'xtatadi.
    """
    MAX_LOGIN_ATTEMPTS = 5

    async with SessionLocal() as db:
        lane = await db.get(Lane, lane_id)
        if lane is None:
            return

        login_ok = False
        last_login_error: LaneError | None = None

        for attempt in range(1, MAX_LOGIN_ATTEMPTS + 1):
            try:
                await _set_status(db, lane, LaneStatus.getting_number)
                await _step_get_number(db, lane, pool)

                # MUHIM: bot "Get code" berishidan OLDIN, BIZ O'ZIMIZ shu
                # raqamga Telegram orqali kod so'raymiz — shundagina
                # keyinroq kodni tasdiqlash uchun kerakli "kalit"
                # (phone_code_hash) bizda bo'ladi.
                requested = await pool.request_own_code(lane.id, lane.phone_number, None)
                if not requested:
                    raise LaneError("O'z kod so'rovimiz muvaffaqiyatsiz bo'ldi")

                await _set_status(db, lane, LaneStatus.logging_in)
                await _step_request_code(db, lane, pool)

                await _set_status(db, lane, LaneStatus.logging_full)
                await _step_full_login(db, lane, pool)

                login_ok = True
                break  # muvaffaqiyatli — sikldan chiqamiz

            except LaneError as e:
                last_login_error = e
                if e.status in (LaneStatus.login_failed, LaneStatus.waiting_stuck):
                    print(f"[Lane {lane_id}] Urinish {attempt}/{MAX_LOGIN_ATTEMPTS} "
                          f"muvaffaqiyatsiz ({e.reason}) — yangi raqam bilan qayta urinamiz")
                    continue  # YANGI raqam bilan qayta urinamiz
                else:
                    # boshqa turdagi xato (masalan bot ulanmagan) —
                    # qayta urinishning ma'nosi yo'q, darrov to'xtaymiz
                    await _fail(db, lane, e.reason, e.status)
                    return
            except Exception as e:
                import traceback
                print(f"[Lane {lane_id}] Kutilmagan xato (login bosqichida): {e}")
                traceback.print_exc()
                await _fail(db, lane, f"Kutilmagan xato: {e}")
                return

        if not login_ok:
            reason = last_login_error.reason if last_login_error else "Login muvaffaqiyatsiz"
            await _fail(db, lane, f"{MAX_LOGIN_ATTEMPTS} marta urinildi, hammasi muvaffaqiyatsiz: {reason}",
                        LaneStatus.login_failed)
            return

        # ── Login muvaffaqiyatli — endi @PremiumBot bosqichi ──
        try:
            await _set_status(db, lane, LaneStatus.premium_pending)
            await _step_buy_premium(db, lane, pool)

            await _set_status(db, lane, LaneStatus.checking)
            confirmed = await _step_check_premium(db, lane, pool)

            if confirmed:
                await _set_status(db, lane, LaneStatus.confirmed)
                await _bump_card_usage(db, lane)
                await stats_service.bump_today(db, lane)
            else:
                await _fail(db, lane, "Check premium tasdiqlamadi")

        except LaneError as e:
            print(f"[Lane {lane_id}] LaneError: {e.reason}")
            await _fail(db, lane, e.reason, e.status)
        except Exception as e:  # kutilmagan xato ham Lane'ni yiqitmasin
            import traceback
            print(f"[Lane {lane_id}] Kutilmagan xato: {e}")
            traceback.print_exc()
            await _fail(db, lane, f"Kutilmagan xato: {e}")


# ═════════════════════════════════════════
# 1-QADAM — /getnumber
# Bot javobi: "📞 Your number:\n\n905063984476" + 3 tugma
# ═════════════════════════════════════════
async def _step_get_number(db: AsyncSession, lane: Lane, pool: TelethonPool) -> None:
    bot = await db.get(NumberBot, lane.bot_id)
    if not bot or not bot.connected:
        raise LaneError("Raqam beruvchi bot ulanmagan")

    access = await _get_access_account(db, lane)
    client = await pool.get_access_client(access)

    async with client.conversation(f"@{bot.username}", timeout=30) as conv:
        await conv.send_message("/getnumber")
        resp = await conv.get_response()

        m = PHONE_PATTERN.search(resp.raw_text or "")
        if not m:
            raise LaneError("Bot raqam qaytarmadi")

        lane.phone_number = m.group(1)
        await db.commit()

        # keyingi qadamda shu xabar ustidan "Get code" bosiladi
        pool.remember_message(lane.id, resp)


# ═════════════════════════════════════════
# 2-QADAM — "Get code" bosish
# Muvaffaqiyat: "Code received... Code: ... Pass: ..."
# Xato:        "Error: Login Code NotFound..."
# ═════════════════════════════════════════
async def _step_request_code(db: AsyncSession, lane: Lane, pool: TelethonPool) -> None:
    bot_msg = pool.get_remembered_message(lane.id)

    try:
        updated_text = await asyncio.wait_for(
            pool.click_button(bot_msg, "get code", until_contains=["code:", "❌", "error"]),
            timeout=settings.login_code_timeout_sec,
        )
    except asyncio.TimeoutError:
        await _press_freeze(pool, bot_msg)
        raise LaneError("Hisob 'waiting' holatida qotdi", LaneStatus.waiting_stuck)

    if ERROR_PATTERN.search(updated_text or ""):
        await _press_freeze(pool, bot_msg)
        raise LaneError(f"Bot xato qaytardi: {(updated_text or '').strip()[:120]}", LaneStatus.login_failed)

    code = CODE_PATTERN.search(updated_text or "")
    passw = PASS_PATTERN.search(updated_text or "")
    if not code or not passw:
        await _press_freeze(pool, bot_msg)
        raise LaneError("Code/Pass ajratib olinmadi", LaneStatus.login_failed)

    lane.login_code = code.group(1)
    lane.login_pass = passw.group(1)
    await db.commit()


# ═════════════════════════════════════════
# 3-QADAM — Code/Pass bilan to'liq login (yangi Telethon sessiyasi)
# ═════════════════════════════════════════
async def _step_full_login(db: AsyncSession, lane: Lane, pool: TelethonPool) -> None:
    session_file = await pool.full_login(
        lane_id=lane.id,
        phone=lane.phone_number,
        code=lane.login_code,
        password=lane.login_pass,
    )
    if not session_file:
        raise LaneError("To'liq login muvaffaqiyatsiz")

    lane.session_file = session_file
    await db.commit()


# ═════════════════════════════════════════
# 4-QADAM — @PremiumBot: invoice, to'lov URL, bank sahifasida OTP
# ═════════════════════════════════════════
async def _step_buy_premium(db: AsyncSession, lane: Lane, pool: TelethonPool) -> None:
    from app.services import bank_page

    card = await db.get(Card, lane.card_id)
    if not card:
        raise LaneError("Karta topilmadi")

    number = decrypt(card.number_enc)
    cvv = decrypt(card.cvv_enc)

    client = await pool.get_lane_client(lane.session_file)

    # MUHIM: bu ikkala chaqiruv (bot bilan gaplashish, tashqi API)
    # ba'zan CHEKSIZ osilib qolishi mumkin (tarmoq, bot javob
    # bermasligi va h.k.) — qattiq vaqt chegarasi bilan o'raymiz,
    # aks holda Lane butun umr "logging_full" holatida qotib qoladi
    try:
        invoice_msg = await asyncio.wait_for(
            pool.premium_flow_start(client, months=1), timeout=30
        )
        form = await asyncio.wait_for(
            pool.premium_enter_card(client, invoice_msg, number=number, exp=card.exp, cvv=cvv),
            timeout=40,
        )
    except asyncio.TimeoutError:
        raise LaneError("@PremiumBot yoki to'lov API javob bermadi (vaqt tugadi)")

    # Karta allaqachon SmartGlocal API orqali yuborilgan bo'lsa
    # (form["native"] == True) — to'lov DARROV tugagan bo'lishi mumkin
    if form.get("immediate"):
        return  # 3-D Secure kerak bo'lmadi, to'lov tugadi

    pay_url = form.get("payUrl")
    if not pay_url:
        raise LaneError("To'lov sahifasi manzili olinmadi")

    is_native = form.get("native", False)

    # ── Agar "native" (SmartGlocal API) usuli bo'lsa — karta ALLAQACHON
    # yuborilgan, bu URL faqat 3-D Secure/OTP sahifasi, karta qayta
    # kiritilmaydi. Agar "native" bo'lmasa (eski, veb-sahifa usuli) —
    # karta hali sahifada kiritilishi kerak. ──
    try:
        if is_native:
            pw, browser, page = await bank_page.open_page_and_wait_otp(pay_url)
        else:
            pw, browser, page = await bank_page.open_page_and_wait_otp(
                pay_url, number=number, exp=card.exp, cvv=cvv,
            )
    except bank_page.BankPageError as e:
        raise LaneError(f"Bank sahifasi ochilmadi: {e}")

    await _set_status(db, lane, LaneStatus.otp_waiting)

    try:
        otp = await _wait_for_otp(db, lane)
        if not otp:
            raise LaneError(f"OTP {settings.otp_wait_timeout_sec}s ichida kelmadi")

        ok = await bank_page.submit_otp_on_page(pw, browser, page, otp)
        if not ok:
            raise LaneError("Bank sahifasida OTP qabul qilinmadi")
    except LaneError:
        try:
            await browser.close()
            await pw.stop()
        except Exception:
            pass
        raise


async def _wait_for_otp(db: AsyncSession, lane: Lane) -> str | None:
    """
    relay_router.py shu Lane uchun OTP kelganda lane.otp_code ni
    to'ldiradi. Biz shu maydonni kutamiz.
    """
    deadline = dt.datetime.utcnow() + dt.timedelta(seconds=settings.otp_wait_timeout_sec)
    while dt.datetime.utcnow() < deadline:
        await db.refresh(lane)
        if lane.otp_code:
            return lane.otp_code
        await asyncio.sleep(1.5)
    return None


# ═════════════════════════════════════════
# 5-QADAM — Check premium
# ═════════════════════════════════════════
async def _step_check_premium(db: AsyncSession, lane: Lane, pool: TelethonPool) -> bool:
    access = await _get_access_account(db, lane)
    bot_msg = pool.get_remembered_message(lane.id)

    result_text = await pool.click_button(
        bot_msg, "check premium",
        until_contains=["activated", "counted", "❌", "error"],
    )
    return bool(CONFIRM_PATTERN.search(result_text or ""))


# ═════════════════════════════════════════
# yordamchilar
# ═════════════════════════════════════════
async def _get_access_account(db: AsyncSession, lane: Lane) -> AccessAccount:
    """
    MUHIM: faqat connected=True bo'lgan hisobni oladi — va owner_id
    orqali QATTIQ filtrlanadi. Shu tufayli egasi va hamkorning
    hisoblari HECH QACHON aralashmaydi, va eski (uzilgan) dostup
    hisob ham noto'g'ri ishlatilmaydi.
    """
    order = await db.get(Order, lane.order_id)
    access = await get_active_access_account(db, order.owner_id)
    if not access:
        raise LaneError("Dostup hisob ulanmagan")
    return access


async def _press_freeze(pool: TelethonPool, bot_msg) -> None:
    # tugma matnida imlo farqi bo'lishi mumkin ("Freeze" / "Freezee") —
    # shuning uchun qisqa "free" bo'yicha moslashuvchan qidiramiz
    try:
        await pool.click_button(bot_msg, "free")
    except Exception:
        pass  # freeze bosilmasa ham Lane baribir failed bo'ladi


async def _set_status(db: AsyncSession, lane: Lane, status: LaneStatus) -> None:
    print(f"[Lane {lane.id}] holat: {status.value}")
    lane.status = status
    if status == LaneStatus.getting_number and not lane.started_at:
        lane.started_at = dt.datetime.utcnow()
    await db.commit()

    # frontendga real vaqtda xabar — Lane kartochkasi darhol yangilanadi
    from app.api.routes.ws_router import manager
    await manager.broadcast(lane.order_id, {
        "type": "lane_update",
        "laneId": lane.id,
        "status": status.value,
        "phoneNumber": lane.phone_number,
    })


async def _fail(db: AsyncSession, lane: Lane, reason: str, status: LaneStatus = LaneStatus.failed) -> None:
    lane.status = status
    lane.error_reason = reason
    lane.finished_at = dt.datetime.utcnow()
    await db.commit()

    from app.api.routes.ws_router import manager
    await manager.broadcast(lane.order_id, {
        "type": "lane_update",
        "laneId": lane.id,
        "status": status.value,
        "errorReason": reason,
    })


async def _bump_card_usage(db: AsyncSession, lane: Lane) -> None:
    card = await db.get(Card, lane.card_id)
    card.used += 1
    lane.finished_at = dt.datetime.utcnow()
    await db.commit()
