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
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
)
from app.core.config import settings
from app.models.tables import AccessAccount, Proxy

# ── ISHLAYDIGAN proxylar ro'yxatini keshlaydi, LEKIN har chaqiruvda
# TASODIFIY BOSHQASINI qaytaradi (aylantirish/rotation) — bitta
# proxy'ni takror-takror ishlatish, Telegram/bank tomonidan bitta
# IP manzilni "shubhali" deb bloklashiga olib kelishi mumkin edi.
_working_proxies_cache: list[tuple] | None = None
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
    Ro'yxatdagi ISHLAYDIGAN proxylarni bir marta topib keshlaydi,
    LEKIN har chaqiruvda ULARDAN TASODIFIY BOSHQASINI qaytaradi
    (aylantirish/rotation) — bir xil IP manzilni takror ishlatish
    o'rniga, har Lane/urinish BOSHQA IP orqali ketadi.

    MUHIM: butun qidiruv 8 soniyadan OSHMAYDI — aks holda frontend
    so'rovi (12s timeout) tugab, "vaqt tugadi" xatosi chiqadi. Agar
    shu vaqt ichida ishlaydigan proxy topilmasa — PROXYSIZ davom
    etiladi (bu login'ning umuman ishlamasligidan yaxshiroq).
    """
    global _working_proxies_cache
    import random

    if _working_proxies_cache:
        return random.choice(_working_proxies_cache)

    async def _search_all():
        candidates = _parse_proxy_list()
        found = []
        for conf in candidates:
            if conf in _tried_dead:
                continue
            ok = await _test_proxy(conf, timeout=2.5)
            if ok:
                found.append(conf)
                if len(found) >= 5:   # 5 tasi yetarli — rotatsiya uchun
                    break
            else:
                _tried_dead.add(conf)
        return found

    try:
        found = await asyncio.wait_for(_search_all(), timeout=8.0)
    except asyncio.TimeoutError:
        found = []

    if found:
        _working_proxies_cache = found
        return random.choice(found)

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
        self._pending_lane_logins: dict[int, dict] = {}  # lane_id -> {client, phone_code_hash}

    # ── dostup hisob mijozi ──
    async def get_access_client(self, access: AccessAccount) -> TelegramClient:
        key = f"access:{access.id}"
        if key in self._clients:
            return self._clients[key]

        # MUHIM: sessiya endi FAYLDA emas, Neon bazasida SATR (StringSession)
        # sifatida saqlanadi — Render'ning vaqtinchalik diskiga bog'liq emas,
        # deploy qilinganda ham yo'qolmaydi.
        proxy_conf = await default_proxy_conf()
        client = TelegramClient(StringSession(access.session_file),
                                  settings.tg_api_id, settings.tg_api_hash,
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

        proxy_conf = await default_proxy_conf()
        client = TelegramClient(StringSession(session_file),
                                  settings.tg_api_id, settings.tg_api_hash,
                                  proxy=proxy_conf)
        await client.connect()
        self._clients[key] = client
        return client

    # ── xabar eslab qolish (Get code / Check premium tugmalari uchun) ──
    def remember_message(self, lane_id: int, message) -> None:
        self._remembered_msgs[lane_id] = message

    def get_remembered_message(self, lane_id: int):
        return self._remembered_msgs.get(lane_id)

    async def click_button(self, message, label_contains: str,
                            until_contains: list[str] | None = None) -> str:
        """
        Xabardagi tugmani topib bosadi, keyin xabarni QAYTA YUKLAB,
        tahrirlangan matnni o'qiydi.

        MUHIM: bot xabarni BIR NECHA MARTA tahrirlashi mumkin
        (masalan avval "kutmoqda...", keyin yakuniy natija) — shuning
        uchun faqat "matn o'zgardimi" emas, balki "kutilayotgan aniq
        so'z (masalan 'Code:' yoki 'Error') paydo bo'ldimi" tekshiriladi.
        `until_contains` berilmasa — birinchi o'zgarishda to'xtaydi
        (eski xatti-harakat, zaxira sifatida).
        """
        if message is None:
            raise RuntimeError("Eslab qolingan xabar topilmadi")

        for row in (message.buttons or []):
            for btn in row:
                if label_contains.lower() in (btn.text or "").lower():
                    await btn.click()

                    client = message.client
                    original_text = getattr(message, "raw_text", "")
                    text = original_text
                    for _ in range(8):  # jami ~8 soniyagacha
                        await asyncio.sleep(1.0)
                        fresh = await client.get_messages(message.chat_id, ids=message.id)
                        fresh_text = getattr(fresh, "raw_text", None) or ""
                        if not fresh_text:
                            continue
                        text = fresh_text
                        if until_contains:
                            if any(kw.lower() in fresh_text.lower() for kw in until_contains):
                                break
                            # kutilayotgan so'z hali yo'q — oraliq holat,
                            # davom etamiz (masalan "kutmoqda...")
                            continue
                        if fresh_text != original_text:
                            break
                    return text

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

    async def request_own_code(self, lane_id: int, phone: str, proxy: Proxy | None) -> bool:
        """
        MUHIM QADAM: bot "Get code" berishidan OLDIN, BIZ O'ZIMIZ shu
        raqamga Telegram orqali kod so'raymiz (send_code_request).
        Shundagina Telegram bizga phone_code_hash beradi — bu "kalit"
        bo'lmasa, keyinroq kodni tasdiqlab bo'lmaydi ("You also need
        to provide a phone_code_hash" xatosi shundan kelib chiqadi).

        Bizning so'rovimiz o'sha raqamga (yoki uni nazorat qilayotgan
        SIM/hisobga) Telegram xabarini yuboradi — raqam-beruvchi bot
        aynan shu xabarni o'qib, "Get code" bosilganda bizga qaytaradi.
        """
        proxy_conf = await self._proxy_conf(proxy)
        client = TelegramClient(
            StringSession(), settings.tg_api_id, settings.tg_api_hash,
            proxy=proxy_conf,
        )
        await client.connect()

        try:
            sent = await client.send_code_request(phone)
        except Exception:
            await client.disconnect()
            return False

        self._pending_lane_logins[lane_id] = {
            "client": client,
            "phone_code_hash": sent.phone_code_hash,
        }
        return True

    async def full_login(self, lane_id: int, phone: str, code: str, password: str | None) -> str | None:
        """
        Bot bergan Code/Pass bilan, BIZ ILGARI (request_own_code orqali)
        ochgan ULANISHNI davom ettirib, to'liq login qilamiz — Telethon
        sign_in bu safar to'g'ri phone_code_hash bilan chaqiriladi.

        Muvaffaqiyat bo'lsa — session fayl nomini qaytaradi.
        Muvaffaqiyatsiz bo'lsa (kod noto'g'ri/eskirgan) — None qaytaradi,
        lane_worker buni "logging_full" bosqichida xato deb hisoblaydi.
        """
        pending = self._pending_lane_logins.get(lane_id)
        if not pending:
            return None

        client: TelegramClient = pending["client"]

        try:
            await client.sign_in(
                phone=phone, code=code,
                phone_code_hash=pending["phone_code_hash"],
            )
        except SessionPasswordNeededError:
            if not password:
                await client.disconnect()
                self._pending_lane_logins.pop(lane_id, None)
                return None
            try:
                await client.sign_in(password=password)
            except Exception:
                await client.disconnect()
                self._pending_lane_logins.pop(lane_id, None)
                return None
        except (PhoneCodeInvalidError, PhoneCodeExpiredError):
            await client.disconnect()
            self._pending_lane_logins.pop(lane_id, None)
            return None

        if not await client.is_user_authorized():
            await client.disconnect()
            self._pending_lane_logins.pop(lane_id, None)
            return None

        # MUHIM: sessiya ENDI MATN (StringSession) sifatida qaytariladi —
        # bu Lane.session_file ustuniga yoziladi va Neon bazasida
        # SAQLANIB QOLADI, Render qayta deploy qilinsa ham yo'qolmaydi.
        session_string = client.session.save()
        self._clients[f"lane:{session_string}"] = client
        self._pending_lane_logins.pop(lane_id, None)
        return session_string

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
        /start yuboriladi. @PremiumBot SUKUT BO'YICHA darhol 1 oylik
        invoice (to'lov taklifi) bilan javob beradi — alohida "1 oylik"
        tugmasi YO'Q, chunki bot standart holatda shu narxni ko'rsatadi
        (masalan "35 990,00 UZS to'lash" tugmasi bilan, ostida esa
        "Yillik obunaga almashtirish" degan boshqa tugma turadi).

        Shuning uchun: agar /start javobining O'ZI invoice bo'lsa —
        hech qanday tugma qidirmasdan, shuni qaytaramiz. Faqat agar
        invoice kelmasa (masalan oldindan boshqa menyu chiqsa), unda
        tugmalar orasidan "to'lash" so'zini qidiramiz (zaxira yo'l).

        Qaytaradi: Telethon Message obyekti (ichida media=MessageMediaInvoice).
        """
        async with client.conversation("@PremiumBot", timeout=20) as conv:
            await conv.send_message("/start")
            resp = await conv.get_response()

            # 1-holat: /start javobining o'zi invoice — ENG KO'P
            # UCHRAYDIGAN holat, hech narsa bosish shart emas
            if getattr(resp, "media", None):
                return resp

            # 2-holat (zaxira): invoice darrov kelmadi — "to'lash"
            # so'zi bor tugmani qidiramiz (masalan boshqa til/format
            # bo'lsa ham ishlashi uchun)
            pay_btn = self._find_button(resp, ["to'lash", "tolash", "pay", "buy"])
            if not pay_btn:
                raise RuntimeError(
                    "Invoice ham, to'lov tugmasi ham topilmadi — "
                    "bot javobi kutilgandan farq qiladi"
                )
            await pay_btn.click()

            invoice_msg = await conv.get_response()
            if not getattr(invoice_msg, "media", None):
                raise RuntimeError("Invoice xabari kelmadi — bot javobi kutilgandan farq qildi")

            return invoice_msg

    async def premium_enter_card(self, client: TelegramClient, invoice_msg,
                                  number: str, exp: str, cvv: str) -> dict:
        """
        Telegram to'lov formasini so'raydi va kartani SmartGlocal API'siga
        yuboradi.

        MANBA: Telegram'ning O'ZINING RASMIY, OCHIQ MANBA KODI —
        BotCheckoutNativeCardEntryControllerNode.swift (Telegram-iOS
        repository, TelegramMessenger tashkiloti). Bu — taxmin emas,
        Telegram ilovasining o'zi ishlatadigan ANIQ, tasdiqlangan format:

          Sarlavha:  "X-PUBLIC-TOKEN": <public_token>   (Bearer EMAS!)
          So'rov:    {"card": {"number":..., "expiration_month":...,
                                 "expiration_year":..., "security_code":...}}
          Javob:     {"data": {"token": "...", "info": {...}}}
          Telegram'ga: {"type": "card", "token": "<token>"}
        """
        from telethon.tl.functions.payments import GetPaymentFormRequest, SendPaymentFormRequest
        from telethon.tl.types import InputInvoiceMessage, InputPaymentCredentials, DataJSON
        import json as _json
        import httpx

        peer = await client.get_input_entity("@PremiumBot")
        invoice = InputInvoiceMessage(peer=peer, msg_id=invoice_msg.id)
        form = await client(GetPaymentFormRequest(invoice=invoice))

        native_provider = getattr(form, "native_provider", None)
        native_params = getattr(form, "native_params", None)
        currency = getattr(getattr(form, "invoice", None), "currency", None)
        print(f"[PAYMENT FORM] provider={native_provider} currency={currency}")

        if native_provider != "smartglocal" or not native_params:
            # native emas — zaxira: veb-sahifa (Playwright) usuliga qaytamiz
            pay_url = getattr(form, "url", None)
            print(f"[PAYMENT FORM] ZAXIRA yo'l (native emas) — pay_url={pay_url}")
            if not pay_url:
                raise RuntimeError("Na native, na veb to'lov manzili topilmadi")
            return {"payUrl": pay_url, "formId": getattr(form, "form_id", None), "native": False}

        params = _json.loads(native_params.data)
        public_token = params.get("public_token")
        tokenize_url = params.get("tokenize_url")
        if not public_token or not tokenize_url:
            raise RuntimeError("SmartGlocal public_token/tokenize_url topilmadi")

        exp_month, _, exp_year = exp.partition("/")

        # ── 1-QADAM: Telegram'ning O'ZI ishlatadigan ANIQ format bilan
        # SmartGlocal'ga karta ma'lumotini yuboramiz ──
        response_cookies = []
        async with httpx.AsyncClient(timeout=20) as http:
            resp = await http.post(
                tokenize_url,
                headers={
                    "Content-Type": "application/json",
                    "X-PUBLIC-TOKEN": public_token,   # Bearer EMAS — aynan shu sarlavha nomi
                },
                json={
                    "card": {
                        "number": number,
                        "expiration_month": exp_month.strip().zfill(2),
                        "expiration_year": exp_year.strip()[-2:].zfill(2),
                        "security_code": cvv,
                    },
                },
            )
            print(f"[SMARTGLOCAL] status={resp.status_code} body={resp.text[:500]}")
            resp.raise_for_status()
            response_json = resp.json()

            # MUHIM: bu javobda o'rnatilgan cookie'larni (agar bo'lsa)
            # ushlab qolamiz — keyinroq Playwright brauzeriga
            # o'tkazamiz, shunda smart-glocal.com domenidagi sessiya
            # UZILMAYDI (httpx va Playwright — ALOHIDA "brauzerlar",
            # cookie avtomatik almashmaydi).
            for cookie in http.cookies.jar:
                response_cookies.append({
                    "name": cookie.name, "value": cookie.value,
                    "domain": cookie.domain, "path": cookie.path or "/",
                })
            if response_cookies:
                print(f"[SMARTGLOCAL] {len(response_cookies)} ta cookie ushlab qolindi")

        # Javob {"data": {"token": "...", "info": {...}}} formatida keladi
        data_obj = response_json.get("data", {})
        token = data_obj.get("token")
        if not token:
            raise RuntimeError(f"SmartGlocal token qaytmadi: {response_json}")

        # ── 2-QADAM: olingan tokenni Telegram'ga yuboramiz —
        # aynan {"type":"card","token":"..."} formatida (Telegram'ning
        # o'zi ishlatadigan format, "id" EMAS) ──
        credentials_data = _json.dumps({"type": "card", "token": token})
        credentials = InputPaymentCredentials(
            save=False,
            data=DataJSON(data=credentials_data),
        )

        send_result = await client(SendPaymentFormRequest(
            form_id=form.form_id,
            invoice=invoice,
            credentials=credentials,
        ))
        print(f"[SMARTGLOCAL] SendPaymentFormRequest natija={send_result}")

        verify_url = getattr(send_result, "url", None)
        if verify_url:
            return {"payUrl": verify_url, "formId": form.form_id, "native": True,
                     "cookies": response_cookies}

        return {"payUrl": None, "formId": form.form_id, "native": True, "immediate": True}



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
