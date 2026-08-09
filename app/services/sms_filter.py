"""Android SmsFilter.kt bilan bir xil mantiq — backendda ham tekshirish uchun."""
import re

_CODE_RE = re.compile(r"\b\d{4,8}\b")


def extract_code(body: str) -> str | None:
    m = _CODE_RE.search(body or "")
    return m.group(0) if m else None
