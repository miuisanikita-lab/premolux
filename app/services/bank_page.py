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
from playwright_stealth import Stealth

# MUHIM: bir vaqtda FAQAT 1 TA Chromium brauzer ishlashiga ruxsat
# beramiz — bepul server (512MB RAM) bir nechta brauzerni bir vaqtda
# ko'tarolmaydi, bu serverni qulatib yuborgan asosiy sabablardan
# biri edi. Boshqa Lane bosqichlari (Telethon, login) baribir
# PARALLEL davom etadi — faqat shu og'ir qism navbatga turadi.
_browser_semaphore = asyncio.Semaphore(1)

# OTP maydonini aniqlashda qidiriladigan so'zlar (ko'p tilda)
CODE_LABEL_HINTS = [
    "verification code", "otp", "one-time", "sms code",
    "tasdiqlash kodi", "kod kiriting", "confirmation code",
    "confirm the operation", "enter the code", "code sent to",
]

# Karta ma'lumoti kiritiladigan maydonlarni topish uchun (SmartGlocal
# kabi "tokenize" sahifalarida) — bular OTP sahifasidan OLDIN keladi
CARD_NUMBER_HINTS = ["card number", "karta raqami", "number"]
CARD_EXPIRY_HINTS = ["mm/yy", "mm / yy", "expiry", "muddat", "exp"]
CARD_CVC_HINTS = ["cvc", "cvv", "security code"]

# Ba'zi banklarda (masalan Uzum) alohida SUBMIT tugmasi YO'Q —
# 5-6 xonali kod TO'LIQ kiritilishi bilan AVTOMATIK yuboriladi.
SUBMIT_TEXT_HINTS = ["submit", "tasdiqlash", "confirm", "davom", "continue", "отправить", "pay", "next"]


class BankPageError(Exception):
    pass


async def _fill_card_fields(page: Page, number: str, exp: str, cvv: str) -> bool:
    """
    "Tokenize" sahifasida (masalan SmartGlocal) karta ma'lumotini
    kiritadi va yuboradi. Bu — OTP so'ralishidan OLDINGI bosqich —
    bank faqat karta ma'lumoti to'g'ri yuborilgandan KEYIN OTP yuboradi.

    exp — "MM/YY" formatida keladi, kerak bo'lsa ikkiga ajratiladi.
    """
    all_inputs = page.locator("input")
    count = await all_inputs.count()

    # DIAGNOSTIKA: sahifadagi BARCHA inputlarni logga chiqaramiz —
    # bu SmartGlocal kabi maxsus vidjetlarda ANIQ nima borligini
    # ko'rish uchun zarur (taxmin qilib bo'lmaydi)
    print(f"[CARD FIELDS] sahifada {count} ta <input> topildi")
    for i in range(count):
        try:
            el = all_inputs.nth(i)
            visible = await el.is_visible()
            attrs = await el.evaluate(
                "e => ({id:e.id, name:e.name, type:e.type, placeholder:e.placeholder, "
                "autocomplete:e.getAttribute('autocomplete'), cls:e.className})"
            )
            print(f"[CARD FIELDS]  #{i} visible={visible} {attrs}")
        except Exception as e:
            print(f"[CARD FIELDS]  #{i} xato: {e}")

    # sahifada IFRAME bo'lsa ham tekshiramiz (SmartGlocal vidjeti
    # ko'pincha iframe ichida bo'ladi)
    print(f"[CARD FIELDS] frame soni: {len(page.frames)}")
    for fi, frame in enumerate(page.frames):
        try:
            frame_inputs = frame.locator("input")
            fcount = await frame_inputs.count()
            if fcount > 0:
                print(f"[CARD FIELDS] frame#{fi} ({frame.url}) — {fcount} ta input")
                for i in range(fcount):
                    try:
                        el = frame_inputs.nth(i)
                        visible = await el.is_visible()
                        attrs = await el.evaluate(
                            "e => ({id:e.id, name:e.name, type:e.type, placeholder:e.placeholder})"
                        )
                        print(f"[CARD FIELDS]  frame#{fi}#{i} visible={visible} {attrs}")
                    except Exception:
                        pass
        except Exception:
            pass

    if count == 0:
        return False

    number_field = expiry_field = cvc_field = None
    for i in range(count):
        el = all_inputs.nth(i)
        try:
            if not await el.is_visible():
                continue
            attrs = await el.evaluate(
                "e => (e.id+' '+e.name+' '+(e.placeholder||'')+' '+(e.getAttribute('autocomplete')||'')).toLowerCase()"
            )
        except Exception:
            continue

        if not number_field and any(h in attrs for h in ["cardnumber", "card number", "card-number", "card.number", "cc-number", "pan"]):
            number_field = el
        elif not expiry_field and any(h in attrs for h in ["exp", "mm/yy", "mm / yy", "cc-exp"]):
            expiry_field = el
        elif not cvc_field and any(h in attrs for h in ["cvc", "cvv", "security", "cc-csc"]):
            cvc_field = el

    # Agar nom bo'yicha aniqlab bo'lmasa — ko'rinadigan inputlarni
    # TARTIB BO'YICHA (karta raqami, muddat, CVV) deb hisoblaymiz —
    # ko'p to'lov formalarida shu tartib standart
    if not (number_field and expiry_field and cvc_field):
        visible = []
        for i in range(count):
            el = all_inputs.nth(i)
            try:
                if await el.is_visible():
                    visible.append(el)
            except Exception:
                continue
        if len(visible) >= 3:
            number_field = number_field or visible[0]
            expiry_field = expiry_field or visible[1]
            cvc_field = cvc_field or visible[2]

    if not (number_field and expiry_field and cvc_field):
        return False

    await number_field.fill(number.replace(" ", ""))
    await expiry_field.fill(exp)
    await cvc_field.fill(cvv)
    print(f"[CARD FILL] 3 ta maydon to'ldirildi, url={page.url}")

    submit = await _find_submit_button(page)
    if submit:
        try:
            submit_text = await submit.inner_text()
        except Exception:
            submit_text = "?"
        print(f"[CARD FILL] 1-tugma topildi: '{submit_text}' — bosilmoqda...")
        await submit.click()
        print(f"[CARD FILL] 1-tugma bosildi, url={page.url}")
    else:
        print("[CARD FILL] Hech qanday tugma TOPILMADI — Enter bosishga urinamiz")
        try:
            await cvc_field.press("Enter")
            print(f"[CARD FILL] Enter bosildi, url={page.url}")
        except Exception as e:
            print(f"[CARD FILL] Enter bosishda xato: {e}")

    # MUHIM: siz tasvirlagan Telegram native oqimiga o'xshab, BU YERDA
    # ham EHTIMOL IKKI BOSQICH bor — birinchi tugma ("Done" kabi)
    # faqat kartani TASDIQLAYDI, ASL to'lov tugmasi ("Pay"/summa
    # ko'rsatilgan) SHUNDAN KEYIN paydo bo'lishi/faollashishi mumkin.
    # BELGILANGAN VAQT KUTISH O'RNIGA — tugma PAYDO BO'LGUNICHA
    # davriy tekshiramiz (topilishi bilanoq DARHOL davom etamiz,
    # topilmasa maksimal 10 soniyagacha kutamiz).
    try:
        pay_hint_btn = None
        pay_text = "?"
        max_wait_s = 10
        check_every_s = 0.4
        elapsed = 0.0
        while elapsed < max_wait_s:
            for hint in SUBMIT_TEXT_HINTS:
                try:
                    btn = page.get_by_role("button", name=re.compile(hint, re.I)).first
                    if await btn.count() > 0 and await btn.is_visible():
                        pay_hint_btn = btn
                        try:
                            pay_text = await btn.inner_text()
                        except Exception:
                            pay_text = "?"
                        break
                except Exception:
                    pass
            if pay_hint_btn:
                break
            await page.wait_for_timeout(int(check_every_s * 1000))
            elapsed += check_every_s

        if pay_hint_btn:
            print(f"[CARD FILL] 2-QADAM: to'lov tugmasi topildi ({elapsed:.1f}s): "
                  f"'{pay_text}' — bosilmoqda...")
            await pay_hint_btn.click()
            print(f"[CARD FILL] 2-tugma bosildi, url={page.url}")
        else:
            print(f"[CARD FILL] 2-QADAM: {max_wait_s}s ichida qo'shimcha "
                  f"to'lov tugmasi topilmadi (kerak bo'lmagan bo'lishi ham mumkin)")
    except Exception as e:
        print(f"[CARD FILL] 2-qadam xatosi: {e}")

    # bosilgandan keyin sahifa javob berishi (yuklanish, yo'naltirish)
    # uchun ozgina kutamiz, keyin holatni yana logga chiqaramiz
    try:
        await page.wait_for_timeout(2500)
    except Exception:
        pass
    print(f"[CARD FILL] YAKUNIY url={page.url}")

    return True


async def _wait_until_url_settles(page: Page, max_wait_ms: int = 15000, check_every_ms: int = 400) -> None:
    """
    Sahifa URL manzili O'ZGARISHNI TO'XTATGUNICHA kutadi — bu
    qayta yo'naltirish (HTTP redirect yoki JavaScript orqali)
    TUGAGANINI ANIQ SO'ZGA TAYANMASDAN, AVTOMATIK aniqlash usuli.

    MUHIM: kamida MIN_INITIAL_WAIT_MS kutilmasdan "barqaror" deb
    e'lon qilinmaydi — aks holda qayta yo'naltirish HALI
    BOSHLANMAGAN bo'lsa ham (masalan sahifa 0.5s dan keyin
    o'zgarsa-yu, biz 0.4s da tekshirib "hech qachon o'zgarmadi"
    deb noto'g'ri xulosa chiqarib qo'yamiz).
    """
    import time as _time
    MIN_INITIAL_WAIT_MS = 1500     # bundan oldin hech qachon "barqaror" deyilmaydi
    REQUIRED_STABLE_CHECKS = 3      # ketma-ket necha marta bir xil bo'lishi kerak

    deadline = _time.time() + max_wait_ms / 1000
    start = _time.time()
    last_url = page.url
    stable_count = 0

    while _time.time() < deadline:
        await asyncio.sleep(check_every_ms / 1000)
        current_url = page.url
        elapsed_ms = (_time.time() - start) * 1000

        if current_url == last_url:
            stable_count += 1
            if stable_count >= REQUIRED_STABLE_CHECKS and elapsed_ms >= MIN_INITIAL_WAIT_MS:
                print(f"[REDIRECT] URL barqarorlashdi: {current_url} ({elapsed_ms:.0f}ms)")
                return
        else:
            stable_count = 0
            last_url = current_url

    print(f"[REDIRECT] {max_wait_ms}ms ichida to'liq barqarorlashmadi, "
          f"hozirgi manzil: {page.url}")


async def open_page_and_wait_otp(url: str, number: str = "", exp: str = "", cvv: str = "",
                                  timeout_ms: int = 25000):
    """
    Bir vaqtda faqat 1 ta brauzer ishlashi uchun semafordan o'tadi,
    keyin haqiqiy ishni _open_page_and_wait_otp_inner() bajaradi.
    """
    async with _browser_semaphore:
        return await _open_page_and_wait_otp_inner(url, number, exp, cvv, timeout_ms)


async def _open_page_and_wait_otp_inner(url: str, number: str, exp: str, cvv: str,
                                         timeout_ms: int):
    """
    1-QADAM: sahifani ochadi.

    Ba'zi to'lov provayderlari (masalan SmartGlocal "tokenize" sahifasi)
    AVVAL karta raqami/muddat/CVV kiritishni talab qiladi — bank
    FAQAT shundan keyin OTP yuboradi. Shuning uchun:
      1. Sahifa ochiladi
      2. Agar karta maydonlari (raqam/muddat/CVV) topilsa — TO'LDIRILADI
         va yuboriladi (bu bankni OTP yuborishga undaydi)
      3. Shundan keyin OTP maydoni qidiriladi

    Agar karta maydonlari topilmasa — bu sahifa allaqachon OTP
    bosqichida deb hisoblanadi (masalan Uzum kabi to'g'ridan-to'g'ri
    OTP so'rovchi holatlar uchun).
    """
    pw = await async_playwright().start()
    browser = None
    try:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage", "--disable-gpu", "--no-sandbox"],
        )
        page = await browser.new_page()

        # MUHIM: bank/to'lov tizimlari (masalan Checkout.com) headless
        # brauzerlarni ANIQLAB, haqiqiy OTP yubormasligi MUMKIN (bu —
        # ehtimoliy sabab, agar OTP hech qachon kelmasa). Stealth
        # kutubxonasi headless brauzerning "sezilarli izlarini"
        # (navigator.webdriver va h.k.) yashiradi — bu KAFOLAT bermaydi,
        # lekin aniqlanish ehtimolini kamaytiradi.
        await Stealth().apply_stealth_async(page)

        await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")

        # MUHIM: ba'zi havolalar (masalan "redirect/form" turi) —
        # OXIRGI, HAQIQIY sahifaga avtomatik qayta yo'naltiradi
        # (redirect). Agar shu qayta yo'naltirish HALI TUGAMAGAN
        # bo'lsa, biz ORALIQ sahifada qolib, uning bo'sh/tayyor
        # bo'lmagan DOM'ini tekshirib, hech narsa topolmasdik.
        # Shuning uchun URL "/redirect/" so'zidan xoli bo'lguncha,
        # yoki domen o'zgargunicha (yakuniy sahifaga yetgunicha) kutamiz.
        # MUHIM: qayta yo'naltirish (redirect) bo'lishi mumkin —
        # aniq so'zga ("/redirect/") tayanish o'rniga, URL
        # o'ZGARISHNI TO'XTATGUNICHA (2 marta ketma-ket bir xil
        # bo'lguncha) AVTOMATIK kutamiz. Bu — qanday turdagi
        # qayta yo'naltirish bo'lishidan qat'i nazar ishlaydi,
        # va URL barqarorlashishi bilanoq (kutish tugashini
        # kutmasdan) DARHOL davom etadi.
        await _wait_until_url_settles(page, max_wait_ms=15000)

        # MUHIM: SmartGlocal kabi provayderlar vidjetni JavaScript orqali,
        # KECHIKISH bilan chizadi ("domcontentloaded" HTML tayyor bo'lganda
        # keladi, lekin JS hali ishlamagan bo'lishi mumkin). Shuning uchun
        # tarmoq harakati tinchigunicha VA input paydo bo'lgunicha kutamiz.
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass  # ba'zi sahifalarda tarmoq hech qachon "tinchimaydi" (masalan doimiy so'rov)
        try:
            await page.wait_for_selector("input", timeout=10000, state="attached")
        except Exception:
            pass  # topilmasa — pastdagi _fill_card_fields baribir 0 ta input deb aniqlaydi

        if number and exp and cvv:
            try:
                filled = await _fill_card_fields(page, number, exp, cvv)
                if filled:
                    # forma yuborilgach, bank OTP sahifasiga o'tishi/
                    # yangilanishi uchun ozgina kutamiz
                    await page.wait_for_timeout(2000)
            except Exception:
                pass  # karta maydonlari topilmasa — OTP to'g'ridan-to'g'ri so'ralgan deb davom etamiz

        field = await _find_otp_field(page, timeout_ms)
        if not field:
            # MUHIM: xato chiqarishdan OLDIN suratga olamiz — bu
            # bizga sahifada HAQIQATAN nima borligini ko'rsatadi
            # (taxmin qilish o'rniga)
            try:
                await page.screenshot(path="/tmp/last_bank_page.png", full_page=True)
                print("[SCREENSHOT] /tmp/last_bank_page.png ga saqlandi")
            except Exception as se:
                print(f"[SCREENSHOT] xato: {se}")
            raise BankPageError("OTP maydoni topilmadi — bank sahifasi kutilgandan farq qildi")

        return pw, browser, page

    except (BankPageError, Exception):
        # MUHIM: xato yoki VAQT TUGASHI (asyncio.wait_for bekor qilishi)
        # sabab bo'lsa ham, brauzer HAR DOIM yopiladi — aks holda
        # "osilib qolgan" Chromium jarayonlari xotirani yeb, serverni
        # qulatib yuborishi mumkin edi (bu aynan shu sabab bo'lgan edi).
        try:
            if browser:
                await browser.close()
        except Exception:
            pass
        try:
            await pw.stop()
        except Exception:
            pass
        raise


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
