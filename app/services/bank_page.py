"""
BANK SAHIFASI — 3-D Secure OTP kiritish (Playwright orqali).

Siz tasvirlagan 8-qadam: Telegram invoice tugagach ochiladigan
"To'lovni yakunlash" oynasi — bu aslida bank saytining o'zi
(masalan NBU + Mastercard ID Check), Telegram emas.

UMUMIY YONDASHUV (avval kelishilgan): har bank sahifasi turlicha
ko'rinsa ham, aksariyati bir xil naqshga amal qiladi — "Verification
Code" nomli yorliq yonida raqamli input, yaqin atrofda SUBMIT tugmasi.
Shu naqshni "aqlli qidiruv" bilan topamiz. Agar umumiy usul ishlamasa,
bank nomi bo'yicha maxsus funksiya qo'shiladi (pastda misol bor).
"""
from __future__ import annotations
import asyncio
import re
from playwright.async_api import async_playwright, Page, TimeoutError as PWTimeout

# OTP maydonini aniqlashda qidiriladigan so'zlar (ko'p tilda)
CODE_LABEL_HINTS = [
    "verification code", "otp", "one-time", "sms code",
    "tasdiqlash kodi", "kod kiriting", "confirmation code",
    "confirm the operation", "enter the code", "code sent to",
]

# Ba'zi banklarda (masalan Uzum) alohida SUBMIT tugmasi YO'Q —
# 5-6 xonali kod TO'LIQ kiritilishi bilan AVTOMATIK yuboriladi.
SUBMIT_TEXT_HINTS = ["submit", "tasdiqlash", "confirm", "davom", "continue", "отправить"]


class BankPageError(Exception):
    pass


async def open_page_and_wait_otp(url: str, timeout_ms: int = 25000):
    """
    1-QADAM (yangi, to'g'ri ketma-ketlik): sahifani OCHADI — bu bankni
    OTP yuborishga undaydi (bank odatda faqat sahifa ochilganda SMS
    yuboradi). Brauzer/sahifa OCHIQ QOLADI, chunki OTP forwarder
    orqali kelguncha kutish kerak. submit_otp_on_page() shu ochiq
    sahifada davom ettiradi.
    """
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page()
    await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")

    field = await _find_otp_field(page, timeout_ms)
    if not field:
        await browser.close()
        await pw.stop()
        raise BankPageError("OTP maydoni topilmadi — bank sahifasi kutilgandan farq qildi")

    return pw, browser, page


async def submit_otp_on_page(pw, browser, page, otp: str) -> bool:
    """
    2-QADAM: OCHIQ sahifaga OTP kiritadi, yuboradi, keyin brauzerni yopadi.

    MUHIM: ba'zi banklarda (masalan Uzum) alohida SUBMIT tugmasi YO'Q —
    kod to'liq kiritilishi bilan AVTOMATIK tasdiqlanadi. Shuning uchun:
      1. Kodni kiritamiz
      2. Agar SUBMIT tugmasi topilsa — bosamiz
      3. Topilmasa — Enter bosamiz (ko'p formalar shunga ham javob beradi)
      4. Ikkalasi ham bo'lmasa — kod kiritilgani YETARLI deb hisoblab,
         muvaffaqiyat deb qaytaramiz (chunki avtomatik forma bo'lishi mumkin)
    """
    try:
        field = await _find_otp_field(page, 5000)
        if not field:
            return False
        await field.fill(otp)

        submit = await _find_submit_button(page)
        if submit:
            await submit.click()
        else:
            # tugma yo'q — Enter bosib ko'ramiz (ko'p forma shunga
            # ham javob beradi), yoki kod avtomatik yuborilgan bo'lishi
            # mumkin (masalan Uzum kabi)
            try:
                await field.press("Enter")
            except Exception:
                pass

        try:
            await page.wait_for_timeout(3000)
        except Exception:
            pass
        return True
    finally:
        await browser.close()
        await pw.stop()


async def submit_otp_generic(url: str, otp: str, timeout_ms: int = 25000) -> bool:
    """
    ESKI, ODDIY USUL (faqat zaxira/sinov uchun) — OTP allaqachon ma'lum
    bo'lganda sahifani ochib, birdaniga kiritadi. MUHIM: productionda
    open_page_and_wait_otp() + submit_otp_on_page() ketma-ketligi
    ishlatiladi, chunki bank ko'pincha faqat sahifa ochilganda SMS
    yuboradi — bu funksiya OTP oldindan ma'lum bo'lgan holatlar uchun.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")

            field = await _find_otp_field(page, timeout_ms)
            if not field:
                raise BankPageError("OTP maydoni topilmadi")

            await field.fill(otp)

            submit = await _find_submit_button(page)
            if not submit:
                raise BankPageError("SUBMIT tugmasi topilmadi")

            await submit.click()

            # muvaffaqiyat belgisi — sahifa yopiladi yoki boshqa manzilga o'tadi
            try:
                await page.wait_for_url(lambda u: u != url, timeout=8000)
            except PWTimeout:
                pass  # ba'zi banklarda url o'zgarmasligi mumkin, xato deb hisoblamaymiz

            return True

        except BankPageError:
            raise
        except Exception as e:
            raise BankPageError(f"Kutilmagan xato: {e}")
        finally:
            await browser.close()


async def _find_otp_field(page: Page, timeout_ms: int):
    """
    1-usul: label matni orqali (eng ishonchli)
    2-usul: input[type=text/tel/number/password] + atrofdagi matn orqali
    3-usul: birinchi ko'rinadigan raqamli input (oxirgi imkoniyat)

    MUHIM: ikkita qo'shimcha holat qo'llab-quvvatlanadi:
      - OTP maydoni ko'pincha type="password" bo'ladi (raqamlarni
        nuqta bilan yashirish uchun — Uzum Bank kabi)
      - Bank 3-D Secure sahifasi ko'pincha IFRAME ichida bo'ladi —
        shuning uchun asosiy sahifadan tashqari BARCHA frame'larni
        ham tekshiramiz
    """
    INPUT_SELECTOR = ("input[type=text], input[type=tel], input[type=number], "
                       "input[type=password], input:not([type])")
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000

    def _all_frames():
        # asosiy sahifa + barcha ichki iframe'lar (bank sahifasi
        # ko'pincha 3-D Secure'ni iframe ichida ko'rsatadi)
        return [page] + page.frames

    while asyncio.get_event_loop().time() < deadline:
        for frame in _all_frames():
            # 1-usul — label matni
            for hint in CODE_LABEL_HINTS:
                try:
                    label = frame.get_by_text(re.compile(hint, re.I)).first
                    if await label.count() > 0:
                        nearby_input = frame.locator(INPUT_SELECTOR).first
                        if await nearby_input.count() > 0 and await nearby_input.is_visible():
                            return nearby_input
                except Exception:
                    pass

            # 2-usul — umuman ko'ringan yagona matn input
            try:
                generic = frame.locator(INPUT_SELECTOR)
                count = await generic.count()
            except Exception:
                continue
            if count == 1:
                if await generic.first.is_visible():
                    return generic.first
            elif count > 1:
                for i in range(count):
                    el = generic.nth(i)
                    try:
                        if not await el.is_visible():
                            continue
                        attrs = await el.evaluate(
                            "e => (e.id + ' ' + e.name + ' ' + (e.placeholder||'')).toLowerCase()"
                        )
                        if any(h.split()[0] in attrs for h in CODE_LABEL_HINTS):
                            return el
                    except Exception:
                        continue

        await asyncio.sleep(0.5)

    return None


async def _find_submit_button(page: Page):
    for hint in SUBMIT_TEXT_HINTS:
        try:
            btn = page.get_by_role("button", name=re.compile(hint, re.I)).first
            if await btn.count() > 0:
                return btn
        except Exception:
            pass
    # zaxira — sahifadagi yagona tugma
    buttons = page.locator("button, input[type=submit]")
    if await buttons.count() == 1:
        return buttons.first
    return None


# ═════════════════════════════════════════
# BANKKA XOS MAXSUS FUNKSIYA MISOLI
# ═════════════════════════════════════════
# Agar umumiy usul ishlamasa, shu tarzda alohida funksiya qo'shiladi
# va lane_worker.py da bank_id ga qarab tanlanadi:
#
# async def submit_otp_nbu(url: str, otp: str) -> bool:
#     async with async_playwright() as p:
#         browser = await p.chromium.launch(headless=True)
#         page = await browser.new_page()
#         await page.goto(url)
#         await page.fill("#otp-input-nbu-maxsus", otp)   # NBU sahifasiga xos ID
#         await page.click("text=SUBMIT")
#         await browser.close()
#         return True
#
# BANK_HANDLERS = {
#     "nbu": submit_otp_nbu,
#     # boshqa banklar shu yerga qo'shiladi
# }


async def submit_otp(url: str, otp: str, bank_id: str | None = None) -> bool:
    """Tashqi chaqiruv nuqtasi — bank_id bo'yicha maxsus yoki umumiy usulni tanlaydi."""
    # BANK_HANDLERS lug'ati to'ldirilgach, shu yerda tekshiriladi:
    # if bank_id in BANK_HANDLERS:
    #     return await BANK_HANDLERS[bank_id](url, otp)
    return await submit_otp_generic(url, otp)
