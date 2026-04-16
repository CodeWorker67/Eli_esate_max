"""Категории грузового временного пропуска (новый сценарий, purpose='0' в БД)."""

from __future__ import annotations

import datetime
import html as html_lib

from maxapi.enums.attachment import AttachmentType
from maxapi.types.attachments.attachment import PhotoAttachmentPayload
from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.attachments.image import Image
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

# Справочное фото типов ТС (Фото #1 в MAX; повторная отправка по token / photo_id)
VEHICLES_PHOTO_ID = 13770305787
VEHICLES_PHOTO_TOKEN = (
    "x9qtfXkSzOVZoTAMZ4Nkj93uGttRllK27xpSNwFj4HsLk0tMLJCJbwq0H3WTkBM3Eq2QE1vZ136wjcmhUETPPWaN/3bBQDpYGPS88QCfcvE="
)
VEHICLES_PHOTO_URL = (
    "https://i.oneme.ru/i?r=BTGBPUwtwgYUeoFhO7rESmr8MhZesICpaXu3PvE1iilYkPtHWYU5h1yf1gZ3ruLDgOk"
)


def vehicles_numbered_image() -> Image:
    return Image(
        type=AttachmentType.IMAGE,
        payload=PhotoAttachmentPayload(
            photo_id=VEHICLES_PHOTO_ID,
            token=VEHICLES_PHOTO_TOKEN,
            url=VEHICLES_PHOTO_URL,
        ),
    )


def vehicles_numbered_message_attachments(kb: InlineKeyboardBuilder) -> list:
    """Вложения для send_message/answer: фото по token MAX и inline-клавиатура."""
    return [vehicles_numbered_image(), kb.as_markup()]

TRUCK_CATEGORY_LABELS: list[str] = [
    "Грузы до 5т",
    "Грузы до 10т",
    "Самосвал до 10м3",
    "Автокран до 5т",
    "Автокран до 10т",
    "Автокран до 25т",
    "Автокран до 40т",
    "Бетон до 5м3",
    "Бетон до 8м3",
    "Автобетононасос",
    "Спецтехника",
]

DCT_PRICE: dict[str, int] = {
    "Грузы до 5т": 1200,
    "Грузы до 10т": 1700,
    "Самосвал до 10м3": 1200,
    "Автокран до 5т": 700,
    "Автокран до 10т": 1200,
    "Автокран до 25т": 1700,
    "Автокран до 40т": 2500,
    "Бетон до 5м3": 1200,
    "Бетон до 8м3": 2300,
    "Автобетононасос": 1700,
    "Спецтехника": 700,
}

# Фиксированная тестовая цена для выбранных user_id в MAX (оплата грузового пропуска).
SPECIAL_PASS_MAX_USER_IDS: frozenset[int] = frozenset({5590779})
SPECIAL_PASS_PRICE_RUBLES: int = 10


def truck_pass_price_rubles(*, payer_max_user_id: int | None, weight_category: str) -> int | None:
    base = DCT_PRICE.get(weight_category)
    if base is None:
        return None
    if payer_max_user_id is not None and payer_max_user_id in SPECIAL_PASS_MAX_USER_IDS:
        return SPECIAL_PASS_PRICE_RUBLES
    return base

PAYLOAD_PREFIX_RC = "truck_cat"
PAYLOAD_PREFIX_SELF = "self_truck_cat"


def truck_category_keyboard(prefix: str) -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    for i, label in enumerate(TRUCK_CATEGORY_LABELS, start=1):
        b.row(
            CallbackButton(
                text=f"{i}. {label}",
                payload=f"{prefix}_{i}",
            )
        )
    return b


def category_from_truck_payload(payload: str, prefix: str) -> str | None:
    if not payload.startswith(f"{prefix}_"):
        return None
    try:
        idx = int(payload.rsplit("_", 1)[-1])
    except ValueError:
        return None
    if 1 <= idx <= len(TRUCK_CATEGORY_LABELS):
        return TRUCK_CATEGORY_LABELS[idx - 1]
    return None


def is_new_truck_pass(tp) -> bool:
    """Новый сценарий грузового пропуска (категории по справочнику); purpose хранит срок как у легковых."""
    if tp.vehicle_type != "truck":
        return False
    wc = (tp.weight_category or "").strip()
    if wc in TRUCK_CATEGORY_LABELS:
        return True
    return (tp.purpose or "") == "0"


def temporary_pass_valid_until_date(tp) -> datetime.date | None:
    """Последний день интервала «дата визита — …» (как у СБ: purpose — число дней от visit_date)."""
    vd = getattr(tp, "visit_date", None)
    if vd is None:
        return None
    purpose = (getattr(tp, "purpose", None) or "").strip()
    days = 1
    if purpose.isdigit():
        days = int(purpose)
    return vd + datetime.timedelta(days=days)


def temp_pass_duration_label(purpose: str | None) -> str:
    p = purpose or ""
    if p in ("6", "13", "29"):
        return f"{int(p) + 1} дней\n"
    if p == "1":
        return "2 дня\n"
    return "1 день\n"


def new_truck_vehicle_block_html(tp) -> str:
    """Блок полей ТС для нового грузового пропуска (после строки «Тип ТС: Грузовой» не дублируем тип)."""
    cat = html_lib.escape(str(tp.weight_category or ""))
    brand = html_lib.escape(str(tp.car_brand or ""))
    num = html_lib.escape(str(tp.car_number or ""))
    vd = tp.visit_date.strftime("%d.%m.%Y") if tp.visit_date else ""
    oc = html_lib.escape(str(tp.owner_comment or "нет"))
    sb = html_lib.escape(str(tp.security_comment or "нет"))
    return (
        f"Категория: {cat}\n"
        f"Марка: {brand}\n"
        f"Номер: {num}\n"
        f"Дата визита: {vd}\n"
        f"Комментарий владельца: {oc}\n"
        f"📝 Комментарий для СБ: {sb}\n"
    )


def new_truck_price_line_html(tp, payer_max_user_id: int | None = None) -> str:
    cat = tp.weight_category or ""
    price = truck_pass_price_rubles(payer_max_user_id=payer_max_user_id, weight_category=cat)
    if price is None:
        return ""
    return f"Тариф: {price} ₽\n"


def security_new_truck_core_html(tp) -> str:
    """Тип ТС и поля карточки для СБ (без шапки резидент/подрядчик)."""
    return f"🚗 Тип ТС: Грузовой\n{new_truck_vehicle_block_html(tp)}"
