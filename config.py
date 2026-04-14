import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _parse_admin_ids(raw: str | None) -> frozenset[int]:
    if not raw or not raw.strip():
        return frozenset()
    out: list[int] = []
    for part in raw.split(","):
        s = part.strip()
        if not s:
            continue
        try:
            uid = int(s)
        except ValueError:
            continue
        if uid > 0:
            out.append(uid)
    return frozenset(out)


MAX_BOT_TOKEN: Optional[str] = os.environ.get("MAX_BOT_TOKEN")
_raw_admins = os.environ.get("ADMIN_IDS") or os.environ.get("ADMIN_ID")
ADMIN_IDS: frozenset[int] = _parse_admin_ids(_raw_admins)

PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "10"))
MAX_TRUCK_PASSES = int(os.environ.get("MAX_TRUCK_PASSES", "0"))
MAX_CAR_PASSES = int(os.environ.get("MAX_CAR_PASSES", "0"))
PASS_TIME = int(os.environ.get("PASS_TIME", "0"))
FUTURE_LIMIT = int(os.environ.get("FUTURE_LIMIT", "0"))
RAZRAB = int(os.environ.get("RAZRAB", "0"))

# ЮKassa (магазин): SHOP_ID и SECRET_KEY из .env
YUKASSA_SHOP_ID: Optional[str] = os.environ.get("SHOP_ID") or os.environ.get("YUKASSA_SHOP_ID")
YUKASSA_SECRET_KEY: Optional[str] = os.environ.get("SECRET_KEY") or os.environ.get("YUKASSA_SECRET_KEY")
YUKASSA_RETURN_URL: str = os.environ.get(
    "YUKASSA_RETURN_URL",
    "https://yookassa.ru",
).strip() or "https://yookassa.ru"

# Чек в запросе создания платежа (54-ФЗ): код ставки НДС по справочнику ЮKassa (1 — без НДС и т.д.)
YUKASSA_RECEIPT_VAT_CODE: int = int(os.environ.get("YUKASSA_RECEIPT_VAT_CODE", "1"))
# Код системы налогообложения (1–6); задайте, если касса/личный кабинет требуют receipt.tax_system_code
_raw_tax_sys = os.environ.get("YUKASSA_RECEIPT_TAX_SYSTEM_CODE", "").strip()
YUKASSA_RECEIPT_TAX_SYSTEM_CODE: Optional[int] = int(_raw_tax_sys) if _raw_tax_sys.isdigit() else None
