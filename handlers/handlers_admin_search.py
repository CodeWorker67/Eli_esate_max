# handlers_admin_search.py
import asyncio
import html as html_lib
import logging
from datetime import datetime, timedelta

from maxapi import F, Router
from maxapi.context.base import BaseContext
from maxapi.context.state_machine import State, StatesGroup
from maxapi.enums.parse_mode import ParseMode
from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from bot import bot
from config import RAZRAB, FUTURE_LIMIT
from db.models import AsyncSessionLocal, Contractor, PermanentPass, Resident, TemporaryPass
from db.util import get_active_admins_managers_sb_tg_ids
from filters import IsAdminOrManager
from temporary_truck import is_new_truck_pass, security_new_truck_core_html
from max_helpers import (
    answer_message,
    callback_ack,
    edit_or_send_callback,
    fio_html,
    profile_link_line_html,
    send_user,
    text_from_message,
    user_id_from_message,
)

router = Router(router_id="admin_search")
router.filter(IsAdminOrManager())


class SearchStates(StatesGroup):
    WAITING_NUMBER = State()
    WAITING_DIGITS = State()
    WAITING_DESTINATION = State()


class DeletePassStates(StatesGroup):
    WAITING_REASON = State()


def get_search_menu() -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="🔍 Поиск по номеру", payload="search_by_number"))
    b.row(CallbackButton(text="🔢 Поиск по цифрам", payload="search_by_digits"))
    b.row(CallbackButton(text="🏡 Поиск по номеру участка", payload="search_by_destination"))
    b.row(CallbackButton(text="📋 Все временные пропуска", payload="all_temp_passes"))
    b.row(CallbackButton(text="⬅️ Назад", payload="back_to_main"))
    return b


def _kb_delete_permanent(pass_id: int) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="❌ Удалить пропуск", payload=f"delete_permanent_{pass_id}"))
    return kb


def _kb_delete_temporary(pass_id: int) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="❌ Удалить пропуск", payload=f"delete_temporary_{pass_id}"))
    return kb


def _kb_back_search_pass() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="⬅️ Назад", payload="search_pass"))
    return kb



@router.message_callback(F.callback.payload == "search_pass")
async def search_pass_menu(event: MessageCallback) -> None:
    try:
        await edit_or_send_callback(
            bot,
            event,
            "Выберите тип поиска пропуска:",
            get_search_menu(),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "search_by_number")
async def start_search_by_number(event: MessageCallback, context: BaseContext) -> None:
    try:
        await context.set_state(SearchStates.WAITING_NUMBER)
        await send_user(bot, event.callback.user.user_id, "Введите номер машины полностью:")
        await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, SearchStates.WAITING_NUMBER)
async def search_by_number(event: MessageCreated, context: BaseContext):
    try:
        car_number = (text_from_message(event) or "").upper().strip()
        today = datetime.now().date()
        found = False
        await context.clear()

        async with AsyncSessionLocal() as session:
            # 1. Поиск постоянных пропусков резидентов
            perm_stmt = select(
                PermanentPass,
                Resident.fio,
                Resident.plot_number,
                Resident.tg_id,
                Resident.first_name,
                Resident.last_name,
            ).join(Resident, PermanentPass.resident_id == Resident.id) \
                .where(
                PermanentPass.car_number == car_number,
                PermanentPass.status == 'approved'
            )
            perm_result = await session.execute(perm_stmt)
            perm_passes = perm_result.all()

            admin_stmt = select(PermanentPass).where(
                PermanentPass.car_number == car_number,
                PermanentPass.status == 'approved',
                PermanentPass.resident_id == None
            )
            admin_result = await session.execute(admin_stmt)
            admin_passes = admin_result.scalars()
            future_limit = today + timedelta(days=FUTURE_LIMIT)

            temp_res_stmt = select(
                TemporaryPass,
                Resident.fio,
                Resident.plot_number,
                Resident.tg_id,
                Resident.first_name,
                Resident.last_name,
            ).join(Resident, TemporaryPass.resident_id == Resident.id).where(
                TemporaryPass.car_number == car_number,
                TemporaryPass.status == 'approved')

            temp_res_passes = []
            temp_res_result = await session.execute(temp_res_stmt)
            for res_pass in temp_res_result:
                temp_pass, fio, plot_number, res_tg, res_fn, res_ln = res_pass
                days_ = temp_pass.purpose
                days = 1
                if days_.isdigit():
                    days = int(days_)
                old_end_date = temp_pass.visit_date + timedelta(days=days)
                if (temp_pass.visit_date <= today and old_end_date >= today) or (
                        temp_pass.visit_date > today and temp_pass.visit_date <= future_limit):
                    temp_res_passes.append(res_pass)

            # 3. Поиск временных пропусков подрядчиков
            temp_contr_stmt = select(
                TemporaryPass,
                Contractor.fio,
                Contractor.company,
                Contractor.position,
                Contractor.tg_id,
                Contractor.first_name,
                Contractor.last_name,
            ) \
                .join(Contractor, TemporaryPass.contractor_id == Contractor.id) \
                .where(
                TemporaryPass.car_number == car_number,
                TemporaryPass.status == 'approved')

            temp_contr_passes = []
            temp_contr_result = await session.execute(temp_contr_stmt)
            for contr_pass in temp_contr_result:
                temp_pass, fio, company, position, con_tg, con_fn, con_ln = contr_pass
                days_ = temp_pass.purpose
                days = 1
                if days_.isdigit():
                    days = int(days_)
                old_end_date = temp_pass.visit_date + timedelta(days=days)
                if (temp_pass.visit_date <= today and old_end_date >= today) or (
                        temp_pass.visit_date > today and temp_pass.visit_date <= future_limit):
                    temp_contr_passes.append(contr_pass)

            temp_staff_stmt = select(TemporaryPass).where(
                TemporaryPass.owner_type == 'staff',
                TemporaryPass.car_number == car_number,
                TemporaryPass.status == 'approved'
            )

            temp_staff_result = await session.execute(temp_staff_stmt)
            temp_staff_passes = []
            for staff_pass in temp_staff_result.scalars().all():
                days_ = staff_pass.purpose
                days = 1
                if days_.isdigit():
                    days = int(days_)
                old_end_date = staff_pass.visit_date + timedelta(days=days)
                if (staff_pass.visit_date <= today and old_end_date >= today) or (
                        staff_pass.visit_date > today and staff_pass.visit_date <= future_limit):
                    temp_staff_passes.append(staff_pass)

            # Обработка постоянных пропусков резидентов
            for pass_data in perm_passes:
                found = True
                perm_pass, fio, plot_number, res_tg, res_fn, res_ln = pass_data
                text = (
                    "🔰 <b>Постоянный пропуск резидента</b>\n\n"
                    f"👤 ФИО резидента: {fio_html(fio, res_tg)}\n"
                    f"{profile_link_line_html(res_fn, res_ln, res_tg, fallback_fio=fio)}"
                    f"🏠 Номер участка: {plot_number}\n"
                    f"🚗 Марка: {perm_pass.car_brand}\n"
                    f"🚙 Модель: {perm_pass.car_model}\n"
                    f"🔢 Номер: {perm_pass.car_number}\n"
                    f"👤 Владелец: {perm_pass.car_owner}\n"
                    f"📝 Комментарий для СБ: {perm_pass.security_comment or 'нет'}"
                )
                keyboard = _kb_delete_permanent(perm_pass.id)
                await asyncio.sleep(0.05)
                await answer_message(event, text, keyboard, parse_mode=ParseMode.HTML)

            # Обработка постоянных пропусков staff
            for pass_data in admin_passes:
                found = True
                perm_pass = pass_data
                text = (
                    "🔰 <b>Постоянный пропуск представителя УК</b>\n\n"
                    f"🚗 Марка: {perm_pass.car_brand}\n"
                    f"🚙 Модель: {perm_pass.car_model}\n"
                    f"🔢 Номер: {perm_pass.car_number}\n"
                    f"🏠 Место назначения: {perm_pass.destination}\n"
                    f"👤 Владелец: {perm_pass.car_owner}\n"
                    f"📝 Комментарий для СБ: {perm_pass.security_comment or 'нет'}"
                )
                keyboard = _kb_delete_permanent(perm_pass.id)
                await asyncio.sleep(0.05)
                await answer_message(event, text, keyboard, parse_mode=ParseMode.HTML)

            # Обработка временных пропусков резидентов
            for pass_data in temp_res_passes:
                found = True
                temp_pass, fio, plot_number, res_tg, res_fn, res_ln = pass_data
                header = (
                    "⏳ <b>Временный пропуск резидента</b>\n\n"
                    f"👤 ФИО резидента: {fio_html(fio, res_tg)}\n"
                    f"{profile_link_line_html(res_fn, res_ln, res_tg, fallback_fio=fio)}"
                    f"🏠 Номер участка: {plot_number}\n"
                )
                if is_new_truck_pass(temp_pass):
                    text = header + security_new_truck_core_html(temp_pass)
                else:
                    days = 1
                    days_ = temp_pass.purpose
                    if days_.isdigit():
                        days = int(days_)
                    if temp_pass.purpose in ['6', '13', '29']:
                        value = f'{int(temp_pass.purpose) + 1} дней\n'
                    elif temp_pass.purpose == '1':
                        value = '2 дня\n'
                    else:
                        value = '1 день\n'
                    text = (
                        header
                        + f"🚗 Тип ТС: {'Легковой' if temp_pass.vehicle_type == 'car' else 'Грузовой'}\n"
                        + f"🔢 Номер: {temp_pass.car_number}\n"
                        + f"🚙 Марка: {temp_pass.car_brand}\n"
                        + f"📦 Тип груза: {temp_pass.cargo_type}\n"
                        + f"📅 Дата визита: {temp_pass.visit_date.strftime('%d.%m.%Y')} - "
                        + f"{(temp_pass.visit_date + timedelta(days=days)).strftime('%d.%m.%Y')}\n"
                        + f"Действие пропуска: {value}"
                        + f"💬 Комментарий владельца: {temp_pass.owner_comment or 'нет'}\n"
                        + f"📝 Комментарий для СБ: {temp_pass.security_comment or 'нет'}"
                    )
                keyboard = _kb_delete_temporary(temp_pass.id)
                await answer_message(event, text, keyboard, parse_mode=ParseMode.HTML)
                await asyncio.sleep(0.05)

            # Обработка временных пропусков подрядчиков
            for pass_data in temp_contr_passes:
                found = True
                temp_pass, fio, company, position, con_tg, con_fn, con_ln = pass_data
                header = (
                    "⏳ <b>Временный пропуск подрядчика</b>\n\n"
                    f"👷 ФИО подрядчика: {fio_html(fio, con_tg)}\n"
                    f"{profile_link_line_html(con_fn, con_ln, con_tg, fallback_fio=fio)}"
                    f"🏢 Компания: {company}\n"
                    f"💼 Должность: {position}\n"
                )
                if is_new_truck_pass(temp_pass):
                    text = header + security_new_truck_core_html(temp_pass)
                else:
                    days = 1
                    days_ = temp_pass.purpose
                    if days_.isdigit():
                        days = int(days_)
                    if temp_pass.purpose in ['6', '13', '29']:
                        value = f'{int(temp_pass.purpose) + 1} дней\n'
                    elif temp_pass.purpose == '1':
                        value = '2 дня\n'
                    else:
                        value = '1 день\n'
                    text = (
                        header
                        + f"🚗 Тип ТС: {'Легковой' if temp_pass.vehicle_type == 'car' else 'Грузовой'}\n"
                        + f"🔢 Номер: {temp_pass.car_number}\n"
                        + f"🚙 Марка: {temp_pass.car_brand}\n"
                        + f"📦 Тип груза: {temp_pass.cargo_type}\n"
                        + f"🏠 Место назначения: {temp_pass.destination}\n"
                        + f"📅 Дата визита: {temp_pass.visit_date.strftime('%d.%m.%Y')} - "
                        + f"{(temp_pass.visit_date + timedelta(days=days)).strftime('%d.%m.%Y')}\n"
                        + f"Действие пропуска: {value}"
                        + f"💬 Комментарий владельца: {temp_pass.owner_comment or 'нет'}\n"
                        + f"📝 Комментарий для СБ: {temp_pass.security_comment or 'нет'}"
                    )
                keyboard = _kb_delete_temporary(temp_pass.id)
                await answer_message(event, text, keyboard, parse_mode=ParseMode.HTML)
                await asyncio.sleep(0.05)

            # Обработка временных пропусков staff
            for temp_pass in temp_staff_passes:
                found = True
                if is_new_truck_pass(temp_pass):
                    text = (
                        "⏳ <b>Временный пропуск от представителя УК</b>\n\n"
                        + security_new_truck_core_html(temp_pass)
                    )
                else:
                    days = 1
                    days_ = temp_pass.purpose
                    if days_.isdigit():
                        days = int(days_)
                    if temp_pass.purpose in ['6', '13', '29']:
                        value = f'{int(temp_pass.purpose) + 1} дней\n'
                    elif temp_pass.purpose == '1':
                        value = '2 дня\n'
                    else:
                        value = '1 день\n'
                    text = (
                        "⏳ <b>Временный пропуск от представителя УК</b>\n\n"
                        f"🚗 Тип ТС: {'Легковой' if temp_pass.vehicle_type == 'car' else 'Грузовой'}\n"
                        f"🔢 Номер: {temp_pass.car_number}\n"
                        f"🚙 Марка: {temp_pass.car_brand}\n"
                        f"📦 Тип груза: {temp_pass.cargo_type}\n"
                        f"🏠 Место назначения: {temp_pass.destination}\n"
                        f"📅 Дата визита: {temp_pass.visit_date.strftime('%d.%m.%Y')} - "
                        f"{(temp_pass.visit_date + timedelta(days=days)).strftime('%d.%m.%Y')}\n"
                        f"Действие пропуска: {value}"
                        f"💬 Комментарий владельца: {temp_pass.owner_comment or 'нет'}\n"
                        f"📝 Комментарий для СБ: {temp_pass.security_comment or 'нет'}"
                    )
                keyboard = _kb_delete_temporary(temp_pass.id)
                await answer_message(event, text, keyboard, parse_mode=ParseMode.HTML)
                await asyncio.sleep(0.05)

            # Формируем итоговое сообщение
            if found:
                reply_text = "🔍 Поиск осуществлен"
            else:
                reply_text = "❌ Совпадений не найдено"

            await answer_message(event, reply_text, _kb_back_search_pass())
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "search_by_digits")
async def start_search_by_digits(event: MessageCallback, context: BaseContext) -> None:
    try:
        await context.set_state(SearchStates.WAITING_DIGITS)
        await send_user(bot, event.callback.user.user_id, "Введите часть номера машины:")
        await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, SearchStates.WAITING_DIGITS)
async def search_by_digits(event: MessageCreated, context: BaseContext):
    try:
        digits = (text_from_message(event) or "").strip()
        today = datetime.now().date()
        await context.clear()
        found = False

        async with AsyncSessionLocal() as session:
            # 1. Поиск постоянных пропусков
            perm_stmt = select(
                PermanentPass,
                Resident.fio,
                Resident.plot_number,
                Resident.tg_id,
                Resident.first_name,
                Resident.last_name,
            ).join(Resident, PermanentPass.resident_id == Resident.id) \
                .where(
                PermanentPass.status == 'approved',
                PermanentPass.car_number.ilike(f"%{digits}%")
            )
            perm_result = await session.execute(perm_stmt)
            perm_passes = perm_result.all()

            admin_stmt = select(PermanentPass).where(
                PermanentPass.car_number.ilike(f"%{digits}%"),
                PermanentPass.status == 'approved',
                PermanentPass.resident_id == None
            )
            admin_result = await session.execute(admin_stmt)
            admin_passes = admin_result.scalars()

            future_limit = today + timedelta(days=FUTURE_LIMIT)

            temp_res_stmt = select(
                TemporaryPass,
                Resident.fio,
                Resident.plot_number,
                Resident.tg_id,
                Resident.first_name,
                Resident.last_name,
            ) \
                .join(Resident, TemporaryPass.resident_id == Resident.id) \
                .where(
                TemporaryPass.status == 'approved',
                TemporaryPass.car_number.ilike(f"%{digits}%")
            )

            temp_res_result = await session.execute(temp_res_stmt)
            temp_res_passes = []
            for res_pass in temp_res_result:
                temp_pass, fio, plot_number, res_tg, res_fn, res_ln = res_pass
                days_ = temp_pass.purpose
                days = 1
                if days_.isdigit():
                    days = int(days_)
                old_end_date = temp_pass.visit_date + timedelta(days=days)
                if (temp_pass.visit_date <= today and old_end_date >= today) or (
                        temp_pass.visit_date > today and temp_pass.visit_date <= future_limit):
                    temp_res_passes.append(res_pass)

            # 3. Поиск временных пропусков подрядчиков
            temp_contr_stmt = select(
                TemporaryPass,
                Contractor.fio,
                Contractor.company,
                Contractor.position,
                Contractor.tg_id,
                Contractor.first_name,
                Contractor.last_name,
            ) \
                .join(Contractor, TemporaryPass.contractor_id == Contractor.id) \
                .where(
                TemporaryPass.status == 'approved',
                TemporaryPass.car_number.ilike(f"%{digits}%")
            )

            temp_contr_result = await session.execute(temp_contr_stmt)
            temp_contr_passes = []
            for contr_pass in temp_contr_result:
                temp_pass, fio, company, position, con_tg, con_fn, con_ln = contr_pass
                days_ = temp_pass.purpose
                days = 1
                if days_.isdigit():
                    days = int(days_)
                old_end_date = temp_pass.visit_date + timedelta(days=days)
                if (temp_pass.visit_date <= today and old_end_date >= today) or (
                        temp_pass.visit_date > today and temp_pass.visit_date <= future_limit):
                    temp_contr_passes.append(contr_pass)

            temp_staff_stmt = select(TemporaryPass).where(
                TemporaryPass.owner_type == 'staff',
                TemporaryPass.status == 'approved',
                TemporaryPass.car_number.ilike(f"%{digits}%")
            )

            temp_staff_result = await session.execute(temp_staff_stmt)
            temp_staff_passes = []
            for staff_pass in temp_staff_result.scalars().all():
                days_ = staff_pass.purpose
                days = 1
                if days_.isdigit():
                    days = int(days_)
                old_end_date = staff_pass.visit_date + timedelta(days=days)
                if (staff_pass.visit_date <= today and old_end_date >= today) or (
                        staff_pass.visit_date > today and staff_pass.visit_date <= future_limit):
                    temp_staff_passes.append(staff_pass)

            # Обработка постоянных пропусков
            for pass_data in perm_passes:
                found = True
                perm_pass, fio, plot_number, res_tg, res_fn, res_ln = pass_data
                text = (
                    "🔰 <b>Постоянный пропуск резидента</b>\n\n"
                    f"👤 ФИО резидента: {fio_html(fio, res_tg)}\n"
                    f"{profile_link_line_html(res_fn, res_ln, res_tg, fallback_fio=fio)}"
                    f"🏠 Номер участка: {plot_number}\n"
                    f"🚗 Марка: {perm_pass.car_brand}\n"
                    f"🚙 Модель: {perm_pass.car_model}\n"
                    f"🔢 Номер: {perm_pass.car_number}\n"
                    f"👤 Владелец: {perm_pass.car_owner}\n"
                    f"📝 Комментарий для СБ: {perm_pass.security_comment or 'нет'}"
                )
                keyboard = _kb_delete_permanent(perm_pass.id)
                await answer_message(event, text, keyboard, parse_mode=ParseMode.HTML)
                await asyncio.sleep(0.05)

            for pass_data in admin_passes:
                found = True
                perm_pass = pass_data
                text = (
                    "🔰 <b>Постоянный пропуск представителя УК</b>\n\n"
                    f"🚗 Марка: {perm_pass.car_brand}\n"
                    f"🚙 Модель: {perm_pass.car_model}\n"
                    f"🔢 Номер: {perm_pass.car_number}\n"
                    f"🏠 Место назначения: {perm_pass.destination}\n"
                    f"👤 Владелец: {perm_pass.car_owner}\n"
                    f"📝 Комментарий для СБ: {perm_pass.security_comment or 'нет'}"
                )
                keyboard = _kb_delete_permanent(perm_pass.id)
                await asyncio.sleep(0.05)
                await answer_message(event, text, keyboard, parse_mode=ParseMode.HTML)

            # Обработка временных пропусков резидентов
            for pass_data in temp_res_passes:
                found = True
                temp_pass, fio, plot_number, res_tg, res_fn, res_ln = pass_data
                header = (
                    "⏳ <b>Временный пропуск резидента</b>\n\n"
                    f"👤 ФИО резидента: {fio_html(fio, res_tg)}\n"
                    f"{profile_link_line_html(res_fn, res_ln, res_tg, fallback_fio=fio)}"
                    f"🏠 Номер участка: {plot_number}\n"
                )
                if is_new_truck_pass(temp_pass):
                    text = header + security_new_truck_core_html(temp_pass)
                else:
                    days = 1
                    days_ = temp_pass.purpose
                    if days_.isdigit():
                        days = int(days_)
                    if temp_pass.purpose in ['6', '13', '29']:
                        value = f'{int(temp_pass.purpose) + 1} дней\n'
                    elif temp_pass.purpose == '1':
                        value = '2 дня\n'
                    else:
                        value = '1 день\n'
                    text = (
                        header
                        + f"🚗 Тип ТС: {'Легковой' if temp_pass.vehicle_type == 'car' else 'Грузовой'}\n"
                        + f"🔢 Номер: {temp_pass.car_number}\n"
                        + f"🚙 Марка: {temp_pass.car_brand}\n"
                        + f"📦 Тип груза: {temp_pass.cargo_type}\n"
                        + f"📅 Дата визита: {temp_pass.visit_date.strftime('%d.%m.%Y')} - "
                        + f"{(temp_pass.visit_date + timedelta(days=days)).strftime('%d.%m.%Y')}\n"
                        + f"Действие пропуска: {value}"
                        + f"💬 Комментарий владельца: {temp_pass.owner_comment or 'нет'}\n"
                        + f"📝 Комментарий для СБ: {temp_pass.security_comment or 'нет'}"
                    )
                keyboard = _kb_delete_temporary(temp_pass.id)
                await answer_message(event, text, keyboard, parse_mode=ParseMode.HTML)
                await asyncio.sleep(0.05)

            # Обработка временных пропусков подрядчиков
            for pass_data in temp_contr_passes:
                found = True
                temp_pass, fio, company, position, con_tg, con_fn, con_ln = pass_data
                header = (
                    "⏳ <b>Временный пропуск подрядчика</b>\n\n"
                    f"👷 ФИО подрядчика: {fio_html(fio, con_tg)}\n"
                    f"{profile_link_line_html(con_fn, con_ln, con_tg, fallback_fio=fio)}"
                    f"🏢 Компания: {company}\n"
                    f"💼 Должность: {position}\n"
                )
                if is_new_truck_pass(temp_pass):
                    text = header + security_new_truck_core_html(temp_pass)
                else:
                    days = 1
                    days_ = temp_pass.purpose
                    if days_.isdigit():
                        days = int(days_)
                    if temp_pass.purpose in ['6', '13', '29']:
                        value = f'{int(temp_pass.purpose) + 1} дней\n'
                    elif temp_pass.purpose == '1':
                        value = '2 дня\n'
                    else:
                        value = '1 день\n'
                    text = (
                        header
                        + f"🚗 Тип ТС: {'Легковой' if temp_pass.vehicle_type == 'car' else 'Грузовой'}\n"
                        + f"🔢 Номер: {temp_pass.car_number}\n"
                        + f"🚙 Марка: {temp_pass.car_brand}\n"
                        + f"📦 Тип груза: {temp_pass.cargo_type}\n"
                        + f"🏠 Место назначения: {temp_pass.destination}\n"
                        + f"📅 Дата визита: {temp_pass.visit_date.strftime('%d.%m.%Y')} - "
                        + f"{(temp_pass.visit_date + timedelta(days=days)).strftime('%d.%m.%Y')}\n"
                        + f"Действие пропуска: {value}"
                        + f"💬 Комментарий владельца: {temp_pass.owner_comment or 'нет'}\n"
                        + f"📝 Комментарий для СБ: {temp_pass.security_comment or 'нет'}"
                    )
                keyboard = _kb_delete_temporary(temp_pass.id)
                await answer_message(event, text, keyboard, parse_mode=ParseMode.HTML)
                await asyncio.sleep(0.05)

            for temp_pass in temp_staff_passes:
                found = True
                if is_new_truck_pass(temp_pass):
                    text = (
                        "⏳ <b>Временный пропуск от представителя УК</b>\n\n"
                        + security_new_truck_core_html(temp_pass)
                    )
                else:
                    days = 1
                    days_ = temp_pass.purpose
                    if days_.isdigit():
                        days = int(days_)
                    if temp_pass.purpose in ['6', '13', '29']:
                        value = f'{int(temp_pass.purpose) + 1} дней\n'
                    elif temp_pass.purpose == '1':
                        value = '2 дня\n'
                    else:
                        value = '1 день\n'
                    text = (
                        "⏳ <b>Временный пропуск от представителя УК</b>\n\n"
                        f"🚗 Тип ТС: {'Легковой' if temp_pass.vehicle_type == 'car' else 'Грузовой'}\n"
                        f"🔢 Номер: {temp_pass.car_number}\n"
                        f"🚙 Марка: {temp_pass.car_brand}\n"
                        f"📦 Тип груза: {temp_pass.cargo_type}\n"
                        f"🏠 Место назначения: {temp_pass.destination}\n"
                        f"📅 Дата визита: {temp_pass.visit_date.strftime('%d.%m.%Y')} - "
                        f"{(temp_pass.visit_date + timedelta(days=days)).strftime('%d.%m.%Y')}\n"
                        f"Действие пропуска: {value}"
                        f"💬 Комментарий владельца: {temp_pass.owner_comment or 'нет'}\n"
                        f"📝 Комментарий для СБ: {temp_pass.security_comment or 'нет'}"
                    )
                keyboard = _kb_delete_temporary(temp_pass.id)
                await answer_message(event, text, keyboard, parse_mode=ParseMode.HTML)
                await asyncio.sleep(0.05)

        # Формируем итоговое сообщение
        if found:
            reply_text = "🔍 Поиск осуществлен"
        else:
            reply_text = "❌ Совпадений не найдено"

        await answer_message(event, reply_text, _kb_back_search_pass())
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "all_temp_passes")
async def show_all_temp_passes(event: MessageCallback):
    try:
        today = datetime.now().date()
        found = False

        async with AsyncSessionLocal() as session:
            future_limit = today + timedelta(days=FUTURE_LIMIT)
            res_stmt = select(
                TemporaryPass,
                Resident.fio,
                Resident.plot_number,
                Resident.tg_id,
                Resident.first_name,
                Resident.last_name,
            ) \
                .join(Resident, TemporaryPass.resident_id == Resident.id) \
                .where(
                TemporaryPass.status == 'approved')

            temp_res_result = await session.execute(res_stmt)
            res_passes = []
            for res_pass in temp_res_result:
                temp_pass, fio, plot_number, res_tg, res_fn, res_ln = res_pass
                days_ = temp_pass.purpose
                days = 1
                if days_.isdigit():
                    days = int(days_)
                old_end_date = temp_pass.visit_date + timedelta(days=days)
                if (temp_pass.visit_date <= today and old_end_date >= today) or (
                        temp_pass.visit_date > today and temp_pass.visit_date <= future_limit):
                    res_passes.append(res_pass)

            # Поиск временных пропусков подрядчиков
            contr_stmt = select(
                TemporaryPass,
                Contractor.fio,
                Contractor.company,
                Contractor.position,
                Contractor.tg_id,
                Contractor.first_name,
                Contractor.last_name,
            ) \
                .join(Contractor, TemporaryPass.contractor_id == Contractor.id) \
                .where(
                TemporaryPass.status == 'approved')

            temp_contr_result = await session.execute(contr_stmt)
            contr_passes = []
            for contr_pass in temp_contr_result:
                temp_pass, fio, company, position, con_tg, con_fn, con_ln = contr_pass
                days_ = temp_pass.purpose
                days = 1
                if days_.isdigit():
                    days = int(days_)
                old_end_date = temp_pass.visit_date + timedelta(days=days)
                if (temp_pass.visit_date <= today and old_end_date >= today) or (
                        temp_pass.visit_date > today and temp_pass.visit_date <= future_limit):
                    contr_passes.append(contr_pass)

            staff_stmt = select(TemporaryPass).where(
                TemporaryPass.owner_type == 'staff',
                TemporaryPass.status == 'approved'
            )

            staff_result = await session.execute(staff_stmt)
            staff_passes = []
            for staff_pass in staff_result.scalars().all():
                days_ = staff_pass.purpose
                days = 1
                if days_.isdigit():
                    days = int(days_)
                old_end_date = staff_pass.visit_date + timedelta(days=days)
                if (staff_pass.visit_date <= today and old_end_date >= today) or (
                        staff_pass.visit_date > today and staff_pass.visit_date <= future_limit):
                    staff_passes.append(staff_pass)

            # Обработка пропусков резидентов
            for pass_data in res_passes:
                found = True
                temp_pass, fio, plot_number, res_tg, res_fn, res_ln = pass_data
                header = (
                    "⏳ <b>Временный пропуск резидента</b>\n\n"
                    f"👤 ФИО резидента: {fio_html(fio, res_tg)}\n"
                    f"{profile_link_line_html(res_fn, res_ln, res_tg, fallback_fio=fio)}"
                    f"🏠 Номер участка: {plot_number}\n"
                )
                if is_new_truck_pass(temp_pass):
                    text = header + security_new_truck_core_html(temp_pass)
                else:
                    days = 1
                    days_ = temp_pass.purpose
                    if days_.isdigit():
                        days = int(days_)
                    if temp_pass.purpose in ['6', '13', '29']:
                        value = f'{int(temp_pass.purpose) + 1} дней\n'
                    elif temp_pass.purpose == '1':
                        value = '2 дня\n'
                    else:
                        value = '1 день\n'
                    text = (
                        header
                        + f"🚗 Тип ТС: {'Легковой' if temp_pass.vehicle_type == 'car' else 'Грузовой'}\n"
                        + f"🔢 Номер: {temp_pass.car_number}\n"
                        + f"🚙 Марка: {temp_pass.car_brand}\n"
                        + f"📦 Тип груза: {temp_pass.cargo_type}\n"
                        + f"📅 Дата визита: {temp_pass.visit_date.strftime('%d.%m.%Y')} - "
                        + f"{(temp_pass.visit_date + timedelta(days=days)).strftime('%d.%m.%Y')}\n"
                        + f"Действие пропуска: {value}"
                        + f"💬 Комментарий владельца: {temp_pass.owner_comment or 'нет'}\n"
                        + f"📝 Комментарий для СБ: {temp_pass.security_comment or 'нет'}"
                    )
                keyboard = _kb_delete_temporary(temp_pass.id)
                await answer_message(event, text, keyboard, parse_mode=ParseMode.HTML)
                await asyncio.sleep(0.05)

            # Обработка временных пропусков подрядчиков
            for pass_data in contr_passes:
                found = True
                temp_pass, fio, company, position, con_tg, con_fn, con_ln = pass_data
                header = (
                    "⏳ <b>Временный пропуск подрядчика</b>\n\n"
                    f"👷 ФИО подрядчика: {fio_html(fio, con_tg)}\n"
                    f"{profile_link_line_html(con_fn, con_ln, con_tg, fallback_fio=fio)}"
                    f"🏢 Компания: {company}\n"
                    f"💼 Должность: {position}\n"
                )
                if is_new_truck_pass(temp_pass):
                    text = header + security_new_truck_core_html(temp_pass)
                else:
                    days = 1
                    days_ = temp_pass.purpose
                    if days_.isdigit():
                        days = int(days_)
                    if temp_pass.purpose in ['6', '13', '29']:
                        value = f'{int(temp_pass.purpose) + 1} дней\n'
                    elif temp_pass.purpose == '1':
                        value = '2 дня\n'
                    else:
                        value = '1 день\n'
                    text = (
                        header
                        + f"🚗 Тип ТС: {'Легковой' if temp_pass.vehicle_type == 'car' else 'Грузовой'}\n"
                        + f"🔢 Номер: {temp_pass.car_number}\n"
                        + f"🚙 Марка: {temp_pass.car_brand}\n"
                        + f"📦 Тип груза: {temp_pass.cargo_type}\n"
                        + f"🏠 Место назначения: {temp_pass.destination}\n"
                        + f"📅 Дата визита: {temp_pass.visit_date.strftime('%d.%m.%Y')} - "
                        + f"{(temp_pass.visit_date + timedelta(days=days)).strftime('%d.%m.%Y')}\n"
                        + f"Действие пропуска: {value}"
                        + f"💬 Комментарий владельца: {temp_pass.owner_comment or 'нет'}\n"
                        + f"📝 Комментарий для СБ: {temp_pass.security_comment or 'нет'}"
                    )
                keyboard = _kb_delete_temporary(temp_pass.id)
                await answer_message(event, text, keyboard, parse_mode=ParseMode.HTML)
                await asyncio.sleep(0.05)

            for temp_pass in staff_passes:
                found = True
                if is_new_truck_pass(temp_pass):
                    text = (
                        "⏳ <b>Временный пропуск от представителя УК</b>\n\n"
                        + security_new_truck_core_html(temp_pass)
                    )
                else:
                    days = 1
                    days_ = temp_pass.purpose
                    if days_.isdigit():
                        days = int(days_)
                    if temp_pass.purpose in ['6', '13', '29']:
                        value = f'{int(temp_pass.purpose) + 1} дней\n'
                    elif temp_pass.purpose == '1':
                        value = '2 дня\n'
                    else:
                        value = '1 день\n'
                    text = (
                        "⏳ <b>Временный пропуск от представителя УК</b>\n\n"
                        f"🚗 Тип ТС: {'Легковой' if temp_pass.vehicle_type == 'car' else 'Грузовой'}\n"
                        f"🔢 Номер: {temp_pass.car_number}\n"
                        f"🚙 Марка: {temp_pass.car_brand}\n"
                        f"📦 Тип груза: {temp_pass.cargo_type}\n"
                        f"🏠 Место назначения: {temp_pass.destination}\n"
                        f"📅 Дата визита: {temp_pass.visit_date.strftime('%d.%m.%Y')} - "
                        f"{(temp_pass.visit_date + timedelta(days=days)).strftime('%d.%m.%Y')}\n"
                        f"Действие пропуска: {value}"
                        f"💬 Комментарий владельца: {temp_pass.owner_comment or 'нет'}\n"
                        f"📝 Комментарий для СБ: {temp_pass.security_comment or 'нет'}"
                    )
                keyboard = _kb_delete_temporary(temp_pass.id)
                await answer_message(event, text, keyboard, parse_mode=ParseMode.HTML)
                await asyncio.sleep(0.05)

            # Формируем итоговое сообщение
            if found:
                reply_text = "🔍 Поиск осуществлен"
            else:
                reply_text = "❌ Актуальных временных пропусков не найдено"

            await answer_message(event, reply_text, _kb_back_search_pass())
            await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "search_by_destination")
async def start_search_by_destination(event: MessageCallback, context: BaseContext) -> None:
    try:
        await context.set_state(SearchStates.WAITING_DESTINATION)
        await send_user(bot, event.callback.user.user_id, "Введите номер участка:")
        await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, SearchStates.WAITING_DESTINATION)
async def search_by_destination(event: MessageCreated, context: BaseContext):
    try:
        dest = (text_from_message(event) or "").strip()
        today = datetime.now().date()
        await context.clear()
        found = False

        async with AsyncSessionLocal() as session:
            # 1. Поиск постоянных пропусков
            perm_stmt = select(
                PermanentPass,
                Resident.fio,
                Resident.plot_number,
                Resident.tg_id,
                Resident.first_name,
                Resident.last_name,
            ).join(Resident, PermanentPass.resident_id == Resident.id) \
                .where(
                PermanentPass.status == 'approved',
                PermanentPass.destination.ilike(f"%{dest}%")
            )
            perm_result = await session.execute(perm_stmt)
            perm_passes = perm_result.all()

            admin_stmt = select(PermanentPass).where(
                PermanentPass.destination.ilike(f"%{dest}%"),
                PermanentPass.status == 'approved',
                PermanentPass.resident_id == None
            )
            admin_result = await session.execute(admin_stmt)
            admin_passes = admin_result.scalars()

            future_limit = today + timedelta(days=FUTURE_LIMIT)

            temp_res_stmt = select(
                TemporaryPass,
                Resident.fio,
                Resident.plot_number,
                Resident.tg_id,
                Resident.first_name,
                Resident.last_name,
            ) \
                .join(Resident, TemporaryPass.resident_id == Resident.id) \
                .where(
                TemporaryPass.status == 'approved',
                TemporaryPass.destination.ilike(f"%{dest}%")
            )

            temp_res_result = await session.execute(temp_res_stmt)
            temp_res_passes = []
            for res_pass in temp_res_result:
                temp_pass, fio, plot_number, res_tg, res_fn, res_ln = res_pass
                days_ = temp_pass.purpose
                days = 1
                if days_.isdigit():
                    days = int(days_)
                old_end_date = temp_pass.visit_date + timedelta(days=days)
                if (temp_pass.visit_date <= today and old_end_date >= today) or (
                        temp_pass.visit_date > today and temp_pass.visit_date <= future_limit):
                    temp_res_passes.append(res_pass)

            # 3. Поиск временных пропусков подрядчиков
            temp_contr_stmt = select(
                TemporaryPass,
                Contractor.fio,
                Contractor.company,
                Contractor.position,
                Contractor.tg_id,
                Contractor.first_name,
                Contractor.last_name,
            ) \
                .join(Contractor, TemporaryPass.contractor_id == Contractor.id) \
                .where(
                TemporaryPass.status == 'approved',
                TemporaryPass.destination.ilike(f"%{dest}%")
            )

            temp_contr_result = await session.execute(temp_contr_stmt)
            temp_contr_passes = []
            for contr_pass in temp_contr_result:
                temp_pass, fio, company, position, con_tg, con_fn, con_ln = contr_pass
                days_ = temp_pass.purpose
                days = 1
                if days_.isdigit():
                    days = int(days_)
                old_end_date = temp_pass.visit_date + timedelta(days=days)
                if (temp_pass.visit_date <= today and old_end_date >= today) or (
                        temp_pass.visit_date > today and temp_pass.visit_date <= future_limit):
                    temp_contr_passes.append(contr_pass)

            temp_staff_stmt = select(TemporaryPass).where(
                TemporaryPass.owner_type == 'staff',
                TemporaryPass.status == 'approved',
                TemporaryPass.destination.ilike(f"%{dest}%")
            )

            temp_staff_result = await session.execute(temp_staff_stmt)
            temp_staff_passes = []
            for staff_pass in temp_staff_result.scalars().all():
                days_ = staff_pass.purpose
                days = 1
                if days_.isdigit():
                    days = int(days_)
                old_end_date = staff_pass.visit_date + timedelta(days=days)
                if (staff_pass.visit_date <= today and old_end_date >= today) or (
                        staff_pass.visit_date > today and staff_pass.visit_date <= future_limit):
                    temp_staff_passes.append(staff_pass)

            # Обработка постоянных пропусков
            for pass_data in perm_passes:
                found = True
                perm_pass, fio, plot_number, res_tg, res_fn, res_ln = pass_data
                text = (
                    "🔰 <b>Постоянный пропуск резидента</b>\n\n"
                    f"👤 ФИО резидента: {fio_html(fio, res_tg)}\n"
                    f"{profile_link_line_html(res_fn, res_ln, res_tg, fallback_fio=fio)}"
                    f"🏠 Номер участка: {plot_number}\n"
                    f"🚗 Марка: {perm_pass.car_brand}\n"
                    f"🚙 Модель: {perm_pass.car_model}\n"
                    f"🔢 Номер: {perm_pass.car_number}\n"
                    f"👤 Владелец: {perm_pass.car_owner}\n"
                    f"📝 Комментарий для СБ: {perm_pass.security_comment or 'нет'}"
                )
                keyboard = _kb_delete_permanent(perm_pass.id)
                await answer_message(event, text, keyboard, parse_mode=ParseMode.HTML)
                await asyncio.sleep(0.05)

            for pass_data in admin_passes:
                found = True
                perm_pass = pass_data
                text = (
                    "🔰 <b>Постоянный пропуск представителя УК</b>\n\n"
                    f"🚗 Марка: {perm_pass.car_brand}\n"
                    f"🚙 Модель: {perm_pass.car_model}\n"
                    f"🔢 Номер: {perm_pass.car_number}\n"
                    f"🏠 Место назначения: {perm_pass.destination}\n"
                    f"👤 Владелец: {perm_pass.car_owner}\n"
                    f"📝 Комментарий для СБ: {perm_pass.security_comment or 'нет'}"
                )
                keyboard = _kb_delete_permanent(perm_pass.id)
                await asyncio.sleep(0.05)
                await answer_message(event, text, keyboard, parse_mode=ParseMode.HTML)

            # Обработка временных пропусков резидентов
            for pass_data in temp_res_passes:
                found = True
                temp_pass, fio, plot_number, res_tg, res_fn, res_ln = pass_data
                header = (
                    "⏳ <b>Временный пропуск резидента</b>\n\n"
                    f"👤 ФИО резидента: {fio_html(fio, res_tg)}\n"
                    f"{profile_link_line_html(res_fn, res_ln, res_tg, fallback_fio=fio)}"
                    f"🏠 Номер участка: {plot_number}\n"
                )
                if is_new_truck_pass(temp_pass):
                    text = header + security_new_truck_core_html(temp_pass)
                else:
                    days = 1
                    days_ = temp_pass.purpose
                    if days_.isdigit():
                        days = int(days_)
                    if temp_pass.purpose in ['6', '13', '29']:
                        value = f'{int(temp_pass.purpose) + 1} дней\n'
                    elif temp_pass.purpose == '1':
                        value = '2 дня\n'
                    else:
                        value = '1 день\n'
                    text = (
                        header
                        + f"🚗 Тип ТС: {'Легковой' if temp_pass.vehicle_type == 'car' else 'Грузовой'}\n"
                        + f"🔢 Номер: {temp_pass.car_number}\n"
                        + f"🚙 Марка: {temp_pass.car_brand}\n"
                        + f"📦 Тип груза: {temp_pass.cargo_type}\n"
                        + f"📅 Дата визита: {temp_pass.visit_date.strftime('%d.%m.%Y')} - "
                        + f"{(temp_pass.visit_date + timedelta(days=days)).strftime('%d.%m.%Y')}\n"
                        + f"Действие пропуска: {value}"
                        + f"💬 Комментарий владельца: {temp_pass.owner_comment or 'нет'}\n"
                        + f"📝 Комментарий для СБ: {temp_pass.security_comment or 'нет'}"
                    )
                keyboard = _kb_delete_temporary(temp_pass.id)
                await answer_message(event, text, keyboard, parse_mode=ParseMode.HTML)
                await asyncio.sleep(0.05)

            # Обработка временных пропусков подрядчиков
            for pass_data in temp_contr_passes:
                found = True
                temp_pass, fio, company, position, con_tg, con_fn, con_ln = pass_data
                header = (
                    "⏳ <b>Временный пропуск подрядчика</b>\n\n"
                    f"👷 ФИО подрядчика: {fio_html(fio, con_tg)}\n"
                    f"{profile_link_line_html(con_fn, con_ln, con_tg, fallback_fio=fio)}"
                    f"🏢 Компания: {company}\n"
                    f"💼 Должность: {position}\n"
                )
                if is_new_truck_pass(temp_pass):
                    text = header + security_new_truck_core_html(temp_pass)
                else:
                    days = 1
                    days_ = temp_pass.purpose
                    if days_.isdigit():
                        days = int(days_)
                    if temp_pass.purpose in ['6', '13', '29']:
                        value = f'{int(temp_pass.purpose) + 1} дней\n'
                    elif temp_pass.purpose == '1':
                        value = '2 дня\n'
                    else:
                        value = '1 день\n'
                    text = (
                        header
                        + f"🚗 Тип ТС: {'Легковой' if temp_pass.vehicle_type == 'car' else 'Грузовой'}\n"
                        + f"🔢 Номер: {temp_pass.car_number}\n"
                        + f"🚙 Марка: {temp_pass.car_brand}\n"
                        + f"📦 Тип груза: {temp_pass.cargo_type}\n"
                        + f"🏠 Место назначения: {temp_pass.destination}\n"
                        + f"📅 Дата визита: {temp_pass.visit_date.strftime('%d.%m.%Y')} - "
                        + f"{(temp_pass.visit_date + timedelta(days=days)).strftime('%d.%m.%Y')}\n"
                        + f"Действие пропуска: {value}"
                        + f"💬 Комментарий владельца: {temp_pass.owner_comment or 'нет'}\n"
                        + f"📝 Комментарий для СБ: {temp_pass.security_comment or 'нет'}"
                    )
                keyboard = _kb_delete_temporary(temp_pass.id)
                await answer_message(event, text, keyboard, parse_mode=ParseMode.HTML)
                await asyncio.sleep(0.05)

            for temp_pass in temp_staff_passes:
                found = True
                if is_new_truck_pass(temp_pass):
                    text = (
                        "⏳ <b>Временный пропуск от представителя УК</b>\n\n"
                        + security_new_truck_core_html(temp_pass)
                    )
                else:
                    days = 1
                    days_ = temp_pass.purpose
                    if days_.isdigit():
                        days = int(days_)
                    if temp_pass.purpose in ['6', '13', '29']:
                        value = f'{int(temp_pass.purpose) + 1} дней\n'
                    elif temp_pass.purpose == '1':
                        value = '2 дня\n'
                    else:
                        value = '1 день\n'
                    text = (
                        "⏳ <b>Временный пропуск от представителя УК</b>\n\n"
                        f"🚗 Тип ТС: {'Легковой' if temp_pass.vehicle_type == 'car' else 'Грузовой'}\n"
                        f"🔢 Номер: {temp_pass.car_number}\n"
                        f"🚙 Марка: {temp_pass.car_brand}\n"
                        f"📦 Тип груза: {temp_pass.cargo_type}\n"
                        f"🏠 Место назначения: {temp_pass.destination}\n"
                        f"📅 Дата визита: {temp_pass.visit_date.strftime('%d.%m.%Y')} - "
                        f"{(temp_pass.visit_date + timedelta(days=days)).strftime('%d.%m.%Y')}\n"
                        f"Действие пропуска: {value}"
                        f"💬 Комментарий владельца: {temp_pass.owner_comment or 'нет'}\n"
                        f"📝 Комментарий для СБ: {temp_pass.security_comment or 'нет'}"
                    )
                keyboard = _kb_delete_temporary(temp_pass.id)
                await answer_message(event, text, keyboard, parse_mode=ParseMode.HTML)
                await asyncio.sleep(0.05)

        # Формируем итоговое сообщение
        if found:
            reply_text = "🔍 Поиск осуществлен"
        else:
            reply_text = "❌ Совпадений не найдено"

        await answer_message(event, reply_text, _kb_back_search_pass())
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


# Хендлеры для удаления пропусков
@router.message_callback(F.callback.payload.startswith("delete_permanent_") | F.callback.payload.startswith("delete_temporary_"))
async def start_delete_pass(event: MessageCallback, context: BaseContext) -> None:
    try:
        pl = event.callback.payload or ""
        parts = pl.split("_")
        pass_type = parts[1]
        pass_id = int(parts[2])
        orig = ""
        mid: str | None = None
        if event.message and event.message.body:
            orig = event.message.body.text or ""
            mid = event.message.body.mid
        await context.update_data(
            pass_type=pass_type,
            pass_id=pass_id,
            message_id=mid,
            original_text=orig,
        )
        await context.set_state(DeletePassStates.WAITING_REASON)
        await send_user(bot, event.callback.user.user_id, "Напишите причину удаления:")
        await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_created(DeletePassStates.WAITING_REASON)
async def process_delete_reason(event: MessageCreated, context: BaseContext):
    try:
        reason = text_from_message(event) or ""
        data = await context.get_data()
        pass_type = data.get('pass_type')
        pass_id = data.get('pass_id')
        message_id = data.get("message_id")
        original_text = data.get("original_text")

        async with AsyncSessionLocal() as session:
            if pass_type == 'permanent':
                pass_record = await session.get(PermanentPass, pass_id)
                if pass_record:
                    # Отправляем уведомление
                    if pass_record.resident_id:
                        # Пропуск резидента
                        resident = await session.get(Resident, pass_record.resident_id)
                        if resident and resident.tg_id:
                            try:
                                await bot.send_message(
                                    user_id=resident.tg_id,
                                    text=(
                                    f"❌ Ваш постоянный пропуск удален.\n\n"
                                    f"Марка: {pass_record.car_brand}\n"
                                    f"Модель: {pass_record.car_model}\n"
                                    f"Номер: {pass_record.car_number}\n"
                                    f"Владелец: {pass_record.car_owner}\n"
                                    f"Пункт назначения: {pass_record.destination}\n"
                                    f"Причина: {reason}"
                                    ),
                                )
                            except Exception as e:
                                logging.error(f"Не удалось отправить сообщение резиденту: {e}")
                    else:
                        # Пропуск представителя УК
                        tg_ids = await get_active_admins_managers_sb_tg_ids()
                        for tg_id in tg_ids:
                            try:
                                await bot.send_message(
                                    user_id=tg_id,
                                    text=(
                                    f"❌ Постоянный пропуск представителя УК удален.\n\n"
                                    f"Марка: {pass_record.car_brand}\n"
                                    f"Модель: {pass_record.car_model}\n"
                                    f"Номер: {pass_record.car_number}\n"
                                    f"Владелец: {pass_record.car_owner}\n"
                                    f"Пункт назначения: {pass_record.destination}\n"
                                    f"Причина: {reason}"
                                    ),
                                    main_menu_attachment=True,
                                )
                                await asyncio.sleep(0.05)
                            except:
                                pass

                    # Удаляем запись
                    await session.delete(pass_record)
                    await session.commit()

            elif pass_type == 'temporary':
                pass_record = await session.get(TemporaryPass, pass_id)
                if pass_record:
                    # Формируем информацию о пропуске
                    if is_new_truck_pass(pass_record):
                        pass_info = (
                            f"Тип ТС: Грузовой\n"
                            f"Категория: {pass_record.weight_category or ''}\n"
                            f"Марка: {pass_record.car_brand}\n"
                            f"Номер: {pass_record.car_number}\n"
                            f"Дата визита: {pass_record.visit_date.strftime('%d.%m.%Y')}\n"
                            f"Комментарий владельца: {pass_record.owner_comment or 'нет'}\n"
                            f"Комментарий для СБ: {pass_record.security_comment or 'нет'}"
                        )
                    else:
                        days = 1
                        days_ = pass_record.purpose
                        if days_.isdigit():
                            days = int(days_)
                        if pass_record.purpose in ['6', '13', '29']:
                            value = f'{int(pass_record.purpose) + 1} дней\n'
                        elif pass_record.purpose == '1':
                            value = '2 дня\n'
                        else:
                            value = '1 день\n'

                        pass_info = (
                            f"Тип ТС: {'Легковой' if pass_record.vehicle_type == 'car' else 'Грузовой'}\n"
                            f"Номер: {pass_record.car_number}\n"
                            f"Марка: {pass_record.car_brand}\n"
                            f"Тип груза: {pass_record.cargo_type}\n"
                            f"Пункт назначения: {pass_record.destination}\n"
                            f"Дата визита: {pass_record.visit_date.strftime('%d.%m.%Y')} - "
                            f"{(pass_record.visit_date + timedelta(days=days)).strftime('%d.%m.%Y')}\n"
                            f"Действие пропуска: {value}"
                            f"Комментарий владельца: {pass_record.owner_comment or 'нет'}\n"
                            f"Комментарий для СБ: {pass_record.security_comment or 'нет'}"
                        )

                    # Отправляем уведомление
                    if pass_record.owner_type == 'resident' and pass_record.resident_id:
                        # Пропуск резидента
                        resident = await session.get(Resident, pass_record.resident_id)
                        if resident and resident.tg_id:
                            try:
                                await bot.send_message(
                                    user_id=resident.tg_id,
                                    text=f"❌ Ваш временный пропуск удален.\n\n{pass_info}\n\nПричина: {reason}",
                                )
                            except Exception as e:
                                logging.error(f"Не удалось отправить сообщение резиденту: {e}")

                    elif pass_record.owner_type == 'contractor' and pass_record.contractor_id:
                        # Пропуск подрядчика
                        contractor = await session.get(Contractor, pass_record.contractor_id)
                        if contractor and contractor.tg_id:
                            try:
                                await bot.send_message(
                                    user_id=contractor.tg_id,
                                    text=f"❌ Ваш временный пропуск удален.\n\n{pass_info}\n\nПричина: {reason}",
                                )
                            except Exception as e:
                                logging.error(f"Не удалось отправить сообщение подрядчику: {e}")

                    else:
                        # Пропуск представителя УК
                        tg_ids = await get_active_admins_managers_sb_tg_ids()
                        for tg_id in tg_ids:
                            try:
                                await bot.send_message(
                                    user_id=tg_id,
                                    text=f"❌ Временный пропуск представителя УК удален.\n\n{pass_info}\n\nПричина: {reason}",
                                    main_menu_attachment=True,
                                )
                                await asyncio.sleep(0.05)
                            except:
                                pass

                    # Удаляем запись
                    await session.delete(pass_record)
                    await session.commit()

        # Редактируем исходное сообщение
        try:
            if message_id:
                await bot.edit_message(
                    message_id=message_id,
                    text=f"{original_text}\n\n❌ Пропуск удален. Причина: {reason}",
                    parse_mode=ParseMode.HTML,
                    attachments=[],
                )
        except Exception as e:
            logging.error(f"Не удалось отредактировать сообщение: {e}")

        await answer_message(event, "✅ Пропуск успешно удален")
        await context.clear()

    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)