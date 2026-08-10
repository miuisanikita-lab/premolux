"""
Barcha maxfiy va muhit-bog'liq sozlamalar shu yerda.
.env faylidan o'qiladi — hech qachon kodga qattiq yozilmaydi.
"""
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── baza ──
    # Replit yoki tez sinov uchun: sqlite+aiosqlite:///./premolux.db
    # Haqiqiy serverda: postgresql+asyncpg://user:pass@host:5432/dbname
    database_url: str = "sqlite+aiosqlite:///./premolux.db"

    # ── Telegram ──
    tg_api_id: int = 0
    tg_api_hash: str = ""
    bot_token: str = ""                  # PremoLux boshqaruv boti (WebApp shu botga bog'lanadi)
    required_channel: str = "@PremoLux"  # majburiy obuna kanali

    # ── xavfsizlik ──
    jwt_secret: str = "CHANGE_ME_IN_PRODUCTION"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24

    # Faqat shu Telegram ID avtomatik "owner" bo'lib ro'yxatdan o'tadi.
    # Boshqa har qanday odam botni ochsa — RAD ETILADI, faqat taklif
    # kodi orqaligina (hamkor/ishchi sifatida) kirishi mumkin bo'ladi.
    owner_tg_id: int = 0

    # ── SMS relay ──
    relay_shared_hint: str = "premolux-relay"  # loglash uchun, xavfsizlik uchun emas

    # ── ish jarayoni ──
    max_parallel_lanes: int = 12         # bir vaqtda nechta bank-oqim ishlay oladi
    otp_wait_timeout_sec: int = 90       # OTP kutish muddati (forwarder orqali)
    login_code_timeout_sec: int = 60     # Telegram login kodini kutish muddati

    # ── sessiyalar qayerda saqlanadi ──
    sessions_dir: str = "./sessions"

    # ── umumiy proxy (Telegram serverga "datacenter IP" deb shubha
    # qilib, kod yuborishni bloklashi mumkin — shuning uchun barcha
    # Telethon ulanishlari shu proxy orqali o'tadi, agar berilgan bo'lsa) ──
    # ── proxy ro'yxati (avtomatik sinov) ──
    # Bitta proxy o'rniga BIR NECHTA proxy ro'yxatini beriladi,
    # kod ularni birma-bir sinab, ISHLAYDIGANINI o'zi tanlaydi —
    # bepul proxylar tez o'chib qolgani uchun qo'lda almashtirish
    # shart emas. Format: "socks5://ip:port,socks5://ip:port,..."
    proxy_list: str = ""

    # (eski, bitta-proxy usuli — proxy_list bo'sh bo'lsa zaxira sifatida)
    proxy_kind: str = ""
    proxy_host: str = ""
    proxy_port: int = 0
    proxy_user: str = ""
    proxy_pass: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
