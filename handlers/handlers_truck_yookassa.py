"""Проверка оплаты грузового пропуска (ЮKassa); роутер без фильтра роли — проверяется владелец пропуска."""

from __future__ import annotations

import asyncio
import datetime
import html as html_lib
import logging

from maxapi import F, Router
from maxapi.enums.parse_mode import ParseMode
from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.updates.message_callback import MessageCallback
from sqlalchemy import select

from bot import bot
from config import RAZRAB, YUKASSA_SECRET_KEY, YUKASSA_SHOP_ID
from db.models import (
    AsyncSessionLocal,
    Contractor,
    Resident,
    TempPassYooKassaPayment,
    TemporaryPass,
)
from db.util import get_active_admins_managers_sb_tg_ids, text_warning
from max_helpers import callback_ack, fio_html, inline_kb, main_menu_inline_button_kb
from staff_temp_pass_notify import staff_auto_approved_temp_pass_html
from yookassa_api import get_payment_status

logger = logging.getLogger(__name__)

router = Router(router_id="truck_yookassa")


def _temp_pass_followup_kb():
    return inline_kb(
        [
            [CallbackButton(text="Оформить временный пропуск", payload="create_temporary_pass")],
            [CallbackButton(text="Назад", payload="back_to_main_menu")],
        ]
    )


async def _notify_resident_truck_paid_approved(
    user_id: int,
    tp: TemporaryPass,
    resident_fio: str,
    payment_rubles: int,
) -> None:
    car_number = (tp.car_number or "").upper()
    kb = _temp_pass_followup_kb()
    await bot.send_message(
        user_id=user_id,
        text=f"✅ Ваш временный пропуск одобрен на машину с номером {car_number}",
        attachments=[kb.as_markup()],
    )
    await bot.send_message(user_id=user_id, text=text_warning)
    intro = f"Пропуск от резидента {fio_html(resident_fio, user_id)} одобрен автоматически"
    staff_text = staff_auto_approved_temp_pass_html(intro, tp, payment_rubles=payment_rubles)
    for tg_id in await get_active_admins_managers_sb_tg_ids():
        try:
            await bot.send_message(
                user_id=tg_id,
                text=staff_text,
                parse_mode=ParseMode.HTML,
                attachments=[main_menu_inline_button_kb().as_markup()],
            )
            await asyncio.sleep(0.05)
        except Exception:
            pass


async def _notify_contractor_truck_paid_approved(
    user_id: int,
    tp: TemporaryPass,
    company: str,
    position: str,
    fio: str,
    payment_rubles: int,
) -> None:
    car_number = (tp.car_number or "").upper()
    kb = _temp_pass_followup_kb()
    await bot.send_message(
        user_id=user_id,
        text=f"✅ Ваш временный пропуск одобрен на машину с номером {car_number}",
        attachments=[kb.as_markup()],
    )
    await bot.send_message(user_id=user_id, text=text_warning)
    intro = (
        f"Пропуск от подрядчика {fio_html(fio, user_id)}, "
        f"{html_lib.escape(company)} — {html_lib.escape(position)} одобрен автоматически"
    )
    staff_text = staff_auto_approved_temp_pass_html(intro, tp, payment_rubles=payment_rubles)
    for tg_id in await get_active_admins_managers_sb_tg_ids():
        try:
            await bot.send_message(
                user_id=tg_id,
                text=staff_text,
                parse_mode=ParseMode.HTML,
                attachments=[main_menu_inline_button_kb().as_markup()],
            )
            await asyncio.sleep(0.05)
        except Exception:
            pass


def _owns_pass(user_id: int, tp: TemporaryPass, resident: Resident | None, contractor: Contractor | None) -> bool:
    if tp.owner_type == "resident" and resident and tp.resident_id == resident.id:
        return resident.tg_id == user_id
    if tp.owner_type == "contractor" and contractor and tp.contractor_id == contractor.id:
        return contractor.tg_id == user_id
    return False


@router.message_callback(F.callback.payload.startswith("yk_check_"))
async def yk_check_truck_payment(event: MessageCallback):
    uid = event.callback.user.user_id
    try:
        pay_row_id = int((event.callback.payload or "").rsplit("_", 1)[-1])
    except ValueError:
        await callback_ack(bot, event, "Некорректный запрос")
        return

    if not YUKASSA_SHOP_ID or not YUKASSA_SECRET_KEY:
        await callback_ack(bot, event, "Оплата недоступна")
        return

    try:
        async with AsyncSessionLocal() as session:
            pay = await session.get(TempPassYooKassaPayment, pay_row_id)
            if not pay:
                await callback_ack(bot, event, "Платёж не найден")
                return

            tp = await session.get(TemporaryPass, pay.temporary_pass_id)
            if not tp:
                await callback_ack(bot, event, "Пропуск не найден")
                return

            res = await session.execute(select(Resident).where(Resident.tg_id == uid))
            resident = res.scalar()
            con = await session.execute(select(Contractor).where(Contractor.tg_id == uid))
            contractor = con.scalar()

            if not _owns_pass(uid, tp, resident, contractor):
                await callback_ack(bot, event, "Нет доступа")
                return

            if tp.status == "approved":
                await callback_ack(bot, event, "Пропуск уже подтверждён")
                return

            if tp.status != "awaiting_payment":
                await callback_ack(bot, event, "Заявка не ожидает оплаты")
                return

            yk_status = await get_payment_status(YUKASSA_SHOP_ID, YUKASSA_SECRET_KEY, pay.yookassa_payment_id)

            if yk_status == "succeeded":
                now = datetime.datetime.now()
                pay.status = "succeeded"
                pay.paid_at = now
                tp.status = "approved"
                tp.time_registration = now
                await session.commit()

                pay_rub = int(pay.amount_kopeks) // 100
                if tp.owner_type == "resident" and resident:
                    await _notify_resident_truck_paid_approved(uid, tp, resident.fio or "", pay_rub)
                elif tp.owner_type == "contractor" and contractor:
                    await _notify_contractor_truck_paid_approved(
                        uid,
                        tp,
                        contractor.company or "",
                        contractor.position or "",
                        contractor.fio or "",
                        pay_rub,
                    )
                await callback_ack(bot, event, "Оплата получена, пропуск подтверждён")
                return

            if yk_status is None:
                await callback_ack(bot, event, "Не удалось проверить оплату, попробуйте позже")
                return

            await callback_ack(
                bot,
                event,
                "Оплаты пока не было, нажмите на кнопку Оплатить",
            )
    except Exception as e:
        logger.exception("yk_check_truck_payment")
        await bot.send_message(user_id=RAZRAB, text=f"{uid} - {e!s}")
        await asyncio.sleep(0.05)
        await callback_ack(bot, event, "Ошибка")
