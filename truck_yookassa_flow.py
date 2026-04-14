"""Создание грузового пропуска с оплатой ЮKassa и текст клавиатуры."""

from __future__ import annotations

import datetime
import html as html_lib
import logging
from dataclasses import dataclass
from typing import Literal

from maxapi.enums.parse_mode import ParseMode
from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.attachments.buttons.link_button import LinkButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from bot import bot
from config import YUKASSA_RETURN_URL, YUKASSA_SECRET_KEY, YUKASSA_SHOP_ID
from db.models import (
    AsyncSessionLocal,
    Contractor,
    Resident,
    TempPassYooKassaPayment,
    TemporaryPass,
)
from temporary_truck import truck_pass_price_rubles
from yookassa_api import create_payment_redirect, normalize_phone_for_yookassa

logger = logging.getLogger(__name__)

OwnerType = Literal["resident", "contractor"]


@dataclass
class NewTruckPassPaymentForm:
    weight_category: str
    car_brand: str
    car_number: str
    owner_comment: str | None
    visit_date: datetime.date
    days_key: str
    destination: str | None


def truck_payment_summary_text(
    *,
    category: str,
    brand: str,
    number: str,
    visit_date: datetime.date,
    amount_rubles: int,
) -> str:
    vd = visit_date.strftime("%d.%m.%Y")
    return (
        "Тип ТС: Грузовой\n"
        f"Категория ТС: {html_lib.escape(category)}\n"
        f"Марка ТС: {html_lib.escape(brand)}\n"
        f"Номер ТС: {html_lib.escape(number)}\n"
        f"Дата заезда: {vd}\n"
        f"Ваша сумма к оплате — {amount_rubles} ₽\n\n"
        "• Нажмите на кнопку «Оплатить»\n"
        "• После оплаты нажмите кнопку «🔄 Проверить оплату»"
    )


def payment_keyboard(confirmation_url: str, local_payment_row_id: int) -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.row(LinkButton(text="Оплатить", url=confirmation_url))
    b.row(
        CallbackButton(
            text="🔄 Проверить оплату",
            payload=f"yk_check_{local_payment_row_id}",
        )
    )
    return b


async def create_awaiting_payment_truck_pass(
    *,
    owner_type: OwnerType,
    tg_user_id: int,
    resident_id: int | None,
    contractor_id: int | None,
    form: NewTruckPassPaymentForm,
) -> tuple[int, int, str] | None:
    """
    Создаёт TemporaryPass (awaiting_payment), платёж в ЮKassa и строку temp_pass_yookassa_payment.
    Возвращает (temporary_pass_id, local_payment_id, confirmation_url) или None.
    """
    if not YUKASSA_SHOP_ID or not YUKASSA_SECRET_KEY:
        logger.error("YooKassa: не заданы SHOP_ID или SECRET_KEY")
        return None

    cat = form.weight_category
    amount = truck_pass_price_rubles(payer_max_user_id=tg_user_id, weight_category=cat)
    if amount is None:
        logger.error("YooKassa: нет цены для категории %s", cat)
        return None

    now = datetime.datetime.now()
    tp = TemporaryPass(
        owner_type=owner_type,
        resident_id=resident_id,
        contractor_id=contractor_id,
        vehicle_type="truck",
        weight_category=cat,
        length_category=None,
        car_number=form.car_number.upper(),
        car_brand=form.car_brand,
        cargo_type=None,
        purpose=form.days_key,
        destination=form.destination,
        visit_date=form.visit_date,
        owner_comment=form.owner_comment,
        status="awaiting_payment",
        created_at=now,
        time_registration=None,
    )

    meta = {
        "temporary_pass_id": "",
        "tg_user_id": str(tg_user_id),
        "owner_type": owner_type,
    }

    async with AsyncSessionLocal() as session:
        session.add(tp)
        await session.flush()
        meta["temporary_pass_id"] = str(tp.id)

        phone_raw: str | None = None
        if owner_type == "resident" and resident_id is not None:
            res = await session.get(Resident, resident_id)
            phone_raw = res.phone if res else None
        elif owner_type == "contractor" and contractor_id is not None:
            ctr = await session.get(Contractor, contractor_id)
            phone_raw = ctr.phone if ctr else None

        receipt_phone = normalize_phone_for_yookassa(phone_raw)
        if not receipt_phone:
            logger.error(
                "YooKassa: нет корректного телефона в БД для чека (owner_type=%s, resident_id=%s, contractor_id=%s)",
                owner_type,
                resident_id,
                contractor_id,
            )
            await session.rollback()
            return None

        pay_description = f"Временный пропуск грузовой ТС, №{tp.id}"
        created = await create_payment_redirect(
            YUKASSA_SHOP_ID,
            YUKASSA_SECRET_KEY,
            amount_rubles=amount,
            return_url=YUKASSA_RETURN_URL,
            description=pay_description,
            metadata=meta,
            receipt_customer_phone=receipt_phone,
        )
        if not created:
            await session.rollback()
            return None
        yk_id, conf_url = created
        row = TempPassYooKassaPayment(
            temporary_pass_id=tp.id,
            yookassa_payment_id=yk_id,
            amount_kopeks=amount * 100,
            status="pending",
            confirmation_url=conf_url,
            created_at=now,
            paid_at=None,
        )
        session.add(row)
        await session.flush()
        pass_id = tp.id
        pay_row_id = row.id
        await session.commit()

    return pass_id, pay_row_id, conf_url


async def send_truck_payment_message(
    *,
    user_id: int,
    form: NewTruckPassPaymentForm,
    confirmation_url: str,
    local_payment_row_id: int,
) -> None:
    amount = truck_pass_price_rubles(payer_max_user_id=user_id, weight_category=form.weight_category)
    if amount is None:
        logger.error("YooKassa: нет цены для категории %s", form.weight_category)
        amount = 0
    text = truck_payment_summary_text(
        category=form.weight_category,
        brand=form.car_brand,
        number=form.car_number.upper(),
        visit_date=form.visit_date,
        amount_rubles=amount,
    )
    kb = payment_keyboard(confirmation_url, local_payment_row_id)
    await bot.send_message(
        user_id=user_id,
        text=text,
        attachments=[kb.as_markup()],
        parse_mode=ParseMode.HTML,
    )
