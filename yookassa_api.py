"""HTTP-клиент к API ЮKassa (создание и проверка платежа)."""

from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from typing import Any

import aiohttp

from config import (
    YUKASSA_RECEIPT_TAX_SYSTEM_CODE,
    YUKASSA_RECEIPT_VAT_CODE,
)

logger = logging.getLogger(__name__)

YOOKASSA_API = "https://api.yookassa.ru/v3"


def normalize_phone_for_yookassa(phone: str | None) -> str | None:
    """
    Номер для receipt.customer.phone: 11 цифр, РФ, вид 79001234567 (как в примерах ЮKassa).
    """
    if phone is None:
        return None
    s = str(phone).strip()
    if not s:
        return None
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if len(digits) == 11:
        if digits.startswith("8"):
            digits = "7" + digits[1:]
        if digits.startswith("7"):
            return digits
        return None
    if len(digits) == 10 and digits[0] == "9":
        return "7" + digits
    return None


def _receipt_payload(
    *,
    customer_phone: str,
    amount_rubles: int,
    item_description: str,
) -> dict[str, Any]:
    value = f"{int(amount_rubles)}.00"
    desc = (item_description or "Оплата услуги")[:128]
    item: dict[str, Any] = {
        "description": desc,
        "quantity": "1.000",
        "amount": {"value": value, "currency": "RUB"},
        "vat_code": YUKASSA_RECEIPT_VAT_CODE,
        "payment_mode": "full_payment",
        "payment_subject": "service",
    }
    receipt: dict[str, Any] = {
        "customer": {"phone": customer_phone},
        "items": [item],
    }
    if YUKASSA_RECEIPT_TAX_SYSTEM_CODE is not None:
        receipt["tax_system_code"] = YUKASSA_RECEIPT_TAX_SYSTEM_CODE
    return receipt


def _basic_auth_header(shop_id: str, secret_key: str) -> str:
    raw = f"{shop_id}:{secret_key}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


async def create_payment_redirect(
    shop_id: str,
    secret_key: str,
    *,
    amount_rubles: int,
    return_url: str,
    description: str,
    metadata: dict[str, str],
    receipt_customer_phone: str,
) -> tuple[str, str] | None:
    """
    Создаёт платёж с подтверждением redirect и данными для фискального чека (54-ФЗ).
    receipt_customer_phone — нормализованный номер (см. normalize_phone_for_yookassa).
    Возвращает (id_платежа_юкассы, confirmation_url) или None при ошибке.
    """
    value = f"{int(amount_rubles)}.00"
    desc = description[:128] if description else "Оплата пропуска"
    body: dict[str, Any] = {
        "amount": {"value": value, "currency": "RUB"},
        "capture": True,
        "confirmation": {"type": "redirect", "return_url": return_url},
        "description": desc,
        "metadata": metadata,
        "receipt": _receipt_payload(
            customer_phone=receipt_customer_phone,
            amount_rubles=amount_rubles,
            item_description=desc,
        ),
    }
    headers = {
        "Authorization": _basic_auth_header(shop_id, secret_key),
        "Content-Type": "application/json",
        "Idempotence-Key": str(uuid.uuid4()),
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{YOOKASSA_API}/payments",
                headers=headers,
                data=json.dumps(body),
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                text = await resp.text()
                if resp.status not in (200, 201):
                    logger.warning("YooKassa create payment HTTP %s: %s", resp.status, text[:500])
                    return None
                data = json.loads(text)
    except Exception:
        logger.exception("YooKassa create payment request failed")
        return None

    pay_id = data.get("id")
    conf = data.get("confirmation") or {}
    url = conf.get("confirmation_url")
    if not pay_id or not url:
        logger.warning("YooKassa create payment missing id/url: %s", data)
        return None
    return str(pay_id), str(url)


async def get_payment_status(shop_id: str, secret_key: str, payment_id: str) -> str | None:
    """Статус платежа: pending, waiting_for_capture, succeeded, canceled и т.д."""
    headers = {
        "Authorization": _basic_auth_header(shop_id, secret_key),
        "Content-Type": "application/json",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{YOOKASSA_API}/payments/{payment_id}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                text = await resp.text()
                if resp.status != 200:
                    logger.warning("YooKassa get payment HTTP %s: %s", resp.status, text[:500])
                    return None
                data = json.loads(text)
    except Exception:
        logger.exception("YooKassa get payment failed")
        return None
    st = data.get("status")
    return str(st) if st is not None else None
