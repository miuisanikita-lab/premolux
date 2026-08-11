"""
Karta raqami/CVV shifrlash — Fernet (simmetrik, AES asosida).

DIQQAT: PREMOLUX_ENC_KEY muhit o'zgaruvchisi PRODUCTION da albatta
o'rnatilishi kerak. O'rnatilmasa, har server qayta ishga tushganda
YANGI kalit yaratiladi va OLDINGI shifrlangan kartalar o'qilmay qoladi.

Kalit yaratish: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import os
from cryptography.fernet import Fernet

_key = os.environ.get("PREMOLUX_ENC_KEY")
if not _key:
    # faqat local sinov uchun — production da .env orqali albatta beriladi
    _key = Fernet.generate_key().decode()

_fernet = Fernet(_key.encode())


def encrypt(value: str) -> str:
    return _fernet.encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet.decrypt(token.encode()).decode()
