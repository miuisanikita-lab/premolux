"""
TELETHON POOL — barcha Telegram bilan bog'liq amallar shu yerda.

Bu qatlam lane_worker.py ni "qanday qilib Telegram bilan gaplashish"
tafsilotlaridan ajratib turadi — worker faqat "nima qilish kerak"ni
biladi, "qanday" qilishni shu fayl hal qiladi.

DIQQAT: quyidagi metodlar HAR BANKNING @PremiumBot invoice/to'lov
oynasiga qarab MOSLASHTIRILISHI kerak (premium_flow_start,
premium_enter_card, premium_submit_otp) — bular Telegram'ning
payments.* MTProto API'siga tegishli va faqat jonli sinovda aniq
yozilishi mumkin. get_access_client, get_lane_client, click_button,
full_login esa Telethon'ning STANDART, hujjatlashtirilgan ishlash
tartibi — botga bog'liq emas, shuning uchun to'liq yozilgan.
"""
from __future__ import annotations
import os
import time
import asyncio
from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
)
from app.core.config import settings
from app.models.tables import AccessAccount, Proxy

# ── ishlaydigan proxy'ni topib, keshlaydi ──
_working_proxy_cache: tuple | None = None
_tried_dead: set = set()


def _parse_proxy_list() -> list[tuple]:
    """settings.proxy_list dagi 'socks5://ip:port,...' qatorini o'qiydi."""
    if not settings.proxy_list:
        return []
    out = []
    for raw in settings.proxy_list.replace("\n", ",").split(","):
        raw = raw.strip()
        if not raw:
            continue
        raw = raw.replace("socks5://", "").replace("http://", "")
        if ":" not in raw:
            continue
        host, _, port = raw.rpartition(":")
        try:
            out.append((2, host, int(port), True, None, None))  # 2 = SOCKS5
        except ValueError:
            continue
    return out


async def _test_proxy(conf: tuple, timeout: float = 2.5) -> bool:
    """
    Proxy orqali Telegram serveriga tez ulanish sinovi.

    MUHIM: asyncio'ning O'ZINING ulanish funksiyasi ishlatiladi
    (bloklovchi socket + run_in_executor EMAS) — chunki bloklovchi
    usulda vaqt tugaganda (wait_for) faqat KUTISH to'xtaydi, lekin
    orqadagi haqiqiy tarmoq ulanishi DAVOM ETAVERADI ("osilib qolgan"
    oqim) — bu serverni resurs yeb, beqarorlashtirar edi (aynan shu
    sabab serverning tasodifiy qulashiga olib kelgan edi).
    """
    try:
        import python_socks
        from python_socks.async_.asyncio import Proxy as AsyncProxy

        proxy = AsyncProxy.from_url(f"socks5://{conf[1]}:{conf[2]}")
        sock = await asyncio.wait_for(
            proxy.connect(dest_host="149.154.167.50", dest_port=443),
            timeout=timeout,
        )
        sock.close()
        return True
    except Exception:
        return False


async def default_proxy_conf():
    """
    Ro'yxatdagi proxylarni birma-bir sinab, birinchi ISHLAYDIGANINI
    qaytaradi (natijani keshlab, qayta-qayta sinamaydi).

    MUHIM: butun qidiruv 8 soniyadan OSHMAYDI — aks holda frontend
    so'rovi (12s timeout) tugab, "vaqt tugadi" xatosi chiqadi. Agar
    shu vaqt ichida ishlaydigan proxy topilmasa — PROXYSIZ davom
    etiladi (bu login'ning umuman ishlamasligidan yaxshiroq).
    Keyingi chaqiruvlarda qidiruv o'lik proxylarni qayta sinamaydi
    (_tried_dead keshlanadi), shuning uchun tezroq bo'ladi.
    """
    global _working_proxy_cache

    if _working_proxy_cache and _working_proxy_cache not in _tried_dead:
        return _working_proxy_cache

    async def _search():
        candidates = _parse_proxy_list()
        for conf in candidates:
            if conf in _tried_dead:
                continue
            ok = await _test_proxy(conf, timeout=2.5)
            if ok:
                return conf
            _tried_dead.add(conf)
        return None

    try:
        found = await asyncio.wait_for(_search(), timeout=8.0)
    except asyncio.TimeoutError:
        found = None

    if found:
        _working_proxy_cache = found
        return found

    # topilmadi — eski bitta-proxy sozlamasiga qaraymiz
    if settings.proxy_host and settings.proxy_port:
        kind_map = {"socks5": 2, "socks4": 1, "http": 3}
        return (
            kind_map.get(settings.proxy_kind, 2),
            settings.proxy_host, settings.proxy_port,
            True, settings.proxy_user or None, settings.proxy_pass or None,
        )
    return None


class TelethonPool:
    """Ochiq Telethon mijozlarini keshlab turadi — har safar qayta ulanmaslik uchun."""

    def __init__(self) -> None:
        self._clients: dict[str, TelegramClient] = {}
        self._remembered_msgs: dict[int, object] = {}   # lane_id -> Telethon message

    # ── dostup hisob mijozi ──
    async def get_access_client(self, access: AccessAccount) -> TelegramClient:
        key = f"access:{access.id}"
        if key in self._clients:
            return self._clients[key]

        session_path = os.path.join(settings.sessions_dir, access.session_file)
        proxy_conf = await default_proxy_conf()
        client = TelegramClient(session_path, settings.tg_api_id, settings.tg_api_hash,
                                  proxy=proxy_conf)
        await client.connect()
        self._clients[key] = client
        return client

    async def disconnect_access_client(self, access: AccessAccount) -> None:
        """
        Yangi dostup hisob ulanganda eskisini uzish uchun (access_service.py
        dan chaqiriladi) — Telethon ulanishini tugatadi va keshdan olib tashlaydi.
        """
        key = f"access:{access.id}"
        client = self._clients.pop(key, None)
        if client:
            await client.disconnect()

    # ── yangi login qilingan Lane mijozi ──
    async def get_lane_client(self, session_file: str) -> TelegramClient:
        key = f"lane:{session_file}"
        if key in self._clients:
            return self._clients[key]

        session_path = os.path.join(settings.sessions_dir, session_file)
        proxy_conf = await default_proxy_conf()
        client = TelegramClient(session_path, settings.tg_api_id, settings.tg_api_hash,
                                  proxy=proxy_conf)
        await client.connect()
        self._clients[key] = client
        return client

    # ── xabar eslab qolish (Get code / Check premium tugmalari uchun) ──
    def remember_message(self, lane_id: int, message) -> None:
        self._remembered_msgs[lane_id] = message

    def get_remembered_message(self, lane_id: int):
        return self._remembered_msgs.get(lane_id)

    async def click_button(self, message, label_contains: str) -> str:
        """
        Xabardagi tugmani matn bo'yicha topib bosadi, natijada
        (tahrirlangan yoki yangi kelgan) matnni qaytaradi.
        """
        if message is None:
            raise RuntimeError("Eslab qolingan xabar topilmadi")

        for row in (message.buttons or []):
            for btn in row:
                if label_contains.lower() in (btn.text or "").lower():
                    await btn.click()
                    # bot odatda xabarni TAHRIRLAYDI — yangilangan holatni o'qiymiz
                    updated = await message.get_message() if hasattr(message, "get_message") else message
                    return getattr(updated, "raw_text", "") or getattr(message, "raw_text", "")

        raise RuntimeError(f"'{label_contains}' tugmasi topilmadi")

    async def _proxy_conf(self, proxy: Proxy | None):
        """
        Telethon proxy formatiga o'giradi. Lane'ga alohida proxy
        tayinlangan bo'lsa — o'shani ishlatadi, aks holda umumiy
        (avtomatik sinovdan o'tgan) proxy'ga qaytadi.
        """
        if not proxy:
            return await default_proxy_conf()
        kind_map = {"socks5": 2, "socks4": 1, "http": 3}  # python-socks/PySocks kodlari
        return (
            kind_map.get(proxy.kind, 2),
            proxy.host,
            proxy.port,
            True,                       # rdns
            proxy.username,
            proxy.password_enc,         # DIQQAT: chaqiruvchi tomonda decrypt qilingan bo'lishi kerak
        )

    async def full_login(self, phone: str, code: str, password: str | None,
                          proxy: Proxy | None) -> str | None:
        """
        Bot bergan Code/Pass bilan BIZ O'ZIMIZ shu Telegram hisobiga
        to'liq, mustaqil login qilamiz — bu Telethon'ning standart
        sign_in oqimi, hech qanday bot-maxsus formatga bog'liq emas.

        Muvaffaqiyat bo'lsa — session fayl nomini qaytaradi.
        Muvaffaqiyatsiz bo'lsa (kod noto'g'ri/eskirgan) — None qaytaradi,
        lane_worker buni "logging_full" bosqichida xato deb hisoblaydi.
        """
        session_file = f"lane_{phone.lstrip('+')}_{int(time.time())}"
        session_path = os.path.join(settings.sessions_dir, session_file)
        os.makedirs(settings.sessions_dir, exist_ok=True)

        proxy_conf = await self._proxy_conf(proxy)
        client = TelegramClient(
            session_path, settings.tg_api_id, settings.tg_api_hash,
            proxy=proxy_conf,
        )
        await client.connect()

        try:
            await client.sign_in(phone=phone, code=code)
        except SessionPasswordNeededError:
            if not password:
                await client.disconnect()
                return None
            try:
                await client.sign_in(password=password)
            except Exception:
                await client.disconnect()
                return None
        except (PhoneCodeInvalidError, PhoneCodeExpiredError):
            await client.disconnect()
            return None

        if not await client.is_user_authorized():
            await client.disconnect()
            return None

        self._clients[f"lane:{session_file}"] = client
        return session_file

    # ── @PremiumBot bilan ishlash ──
    #
    # QUYIDAGI ISHLASH TARTIBI Telegram'ning hujjatlashtirilgan
    # payments.* MTProto oqimiga asoslangan (Bot API "Payments"
    # bo'limi bilan bir xil mantiq, lekin Telethon orqali):
    #
    #   1. @PremiumBot ga /start yuboriladi
    #   2. Javobdagi invoice xabari topiladi (odatda MessageMediaInvoice)
    #   3. payments.GetPaymentForm — to'lov formasi so'raladi
    #   4. payments.SendPaymentForm — karta ma'lumoti bilan yuboriladi
    #   5. Agar 3-D Secure kerak bo'lsa — javobda "url" keladi,
    #      shu URL Playwright'ga (bank_page.py) uzatiladi
    #
    # BU YERDA XATO CHIQISHI KUTILADI birinchi jonli sinovda —
    # sababi: @PremiumBot invoice xabarini ANIQ qanday formatda
    # yuborishi (tugma bosilganidan keyinmi, to'g'ridan-to'g'rimi)
    # faqat amalda ko'rinadi. Xato chiqsa, shu funksiya ichidagi
    # bosqichlardan qaysi biri kutilganidan farq qilganini ko'rib,
    # tez tuzatish mumkin bo'ladi.

    async def premium_flow_start(self, client: TelegramClient, months: int = 1):
        """
        /start -> '1 oylik' tugmasini bosish -> invoice xabarini olish.
        Qaytaradi: Telethon Message obyekti (ichida media=MessageMediaInvoice bo'lishi kerak).
        """
        async with client.conversation("@PremiumBot", timeout=20) as conv:
            await conv.send_message("/start")
            resp = await conv.get_response()

            month_btn = self._find_button(resp, ["1 oylik", "1 month", "oylik"])
            if not month_btn:
                raise RuntimeError("'1 oylik' tugmasi topilmadi")
            await month_btn.click()

            invoice_msg = await conv.get_response()

            if not getattr(invoice_msg, "media", None):
                raise RuntimeError("Invoice xabari kelmadi — bot javobi kutilgandan farq qildi")

            return invoice_msg

    async def premium_enter_card(self, client: TelegramClient, invoice_msg,
                                  number: str, exp: str, cvv: str) -> dict:
        """
        Telegram to'lov formasini so'raydi va karta ma'lumotini yuboradi.

        DIQQAT: Telethon'da bu odatda quyidagicha ishlaydi:
          from telethon.tl.functions.payments import (
              GetPaymentFormRequest, SendPaymentFormRequest,
              ValidateRequestedInfoRequest,
          )
          from telethon.tl.types import InputPaymentCredentialsAndroidPay  # yoki mos tur

        Karta ma'lumotini TO'G'RIDAN-TO'G'RI Telegram'ga yuborish odatda
        RUXSAT ETILMAYDI (PCI-DSS talablariga ko'ra) — buning o'rniga
        Telegram invoice.receipt_msg_id yoki invoice link orqali
        TASHQI to'lov provayderi (bank) sahifasiga o'tkaziladi.

        Shuning uchun bu funksiyaning haqiqiy vazifasi — to'lov
        formasidan 3-D Secure URL'ni OLIB, bank_page.py ga uzatish.
        Kod skeleti quyida, aniq maydon nomlari sinovda tasdiqlanadi.
        """
        from telethon.tl.functions.payments import GetPaymentFormRequest

        peer = await client.get_input_entity("@PremiumBot")
        form = await client(GetPaymentFormRequest(peer=peer, msg_id=invoice_msg.id))

        # form.url yoki form.invoice orqali to'lov sahifasi manzili olinadi —
        # aniq maydon nomi Telethon versiyasiga qarab farq qilishi mumkin,
        # birinchi sinovda `dir(form)` bilan tekshiriladi
        pay_url = getattr(form, "url", None)
        if not pay_url:
            raise RuntimeError(
                "To'lov formasi URL qaytarmadi — GetPaymentFormRequest "
                "javobini log qilib, aniq maydonni topish kerak"
            )

        return {"payUrl": pay_url, "formId": form.form_id if hasattr(form, "form_id") else None}

    async def premium_submit_otp(self, client: TelegramClient, otp: str) -> None:
        """
        Bu funksiya ATAYIN bo'sh qoldirilgan — chunki OTP odatda
        Telegram ICHIDA emas, balki premium_enter_card qaytargan
        pay_url orqali ochiladigan BANK sahifasida (3-D Secure)
        kiritiladi. Haqiqiy OTP kiritish app/services/bank_page.py
        dagi submit_otp() orqali amalga oshadi — Playwright bilan.

        lane_worker.py buni to'g'ridan-to'g'ri chaqirmaydi, o'rniga:
          pay_url = (premium_enter_card natijasi)
          await bank_page.submit_otp(pay_url, otp, bank_id=card.bank_id)
        """
        raise RuntimeError(
            "Bu metod ishlatilmasligi kerak — bank_page.submit_otp() ni chaqiring"
        )

    def _find_button(self, message, label_variants: list[str]):
        """Bir nechta mumkin bo'lgan matn variantidan birortasiga mos tugmani topadi."""
        if not message.buttons:
            return None
        for row in message.buttons:
            for btn in row:
                text = (btn.text or "").lower()
                if any(v.lower() in text for v in label_variants):
                    return btn
        return None
