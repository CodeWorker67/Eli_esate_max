# handlers_admin_temporary_pass.py
import asyncio
import html as html_lib

from maxapi.context.base import BaseContext
from maxapi.context.state_machine import State, StatesGroup
from maxapi.enums.attachment import AttachmentType
from maxapi.enums.parse_mode import ParseMode
from maxapi.filters.command import Command, CommandStart
from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.attachments.file import File
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
import logging
import datetime

from maxapi import F, Router
from sqlalchemy import select, func, delete

from bot import bot
from max_helpers import (
    answer_message,
    inline_kb,
    callback_ack,
    edit_or_send_callback,
    fio_html,
    main_menu_inline_button_kb,
    profile_link_line_html,
    send_user,
    states_in_group,
    text_from_message,
    user_id_from_message,
)
from date_parser import parse_date
from db.models import AsyncSessionLocal, Resident, Contractor, TemporaryPass
from config import ADMIN_IDS, PAGE_SIZE, RAZRAB
from db.util import get_active_admins_managers_sb_tg_ids, text_warning
from filters import IsAdminOrManager
from handlers.handlers_admin_permanent_pass import passes_menu
from temporary_truck import (
    is_new_truck_pass,
    new_truck_price_line_html,
    new_truck_vehicle_block_html,
)

router = Router(router_id="admin_temporary_pass")
router.filter(IsAdminOrManager())


def _uid(ev: MessageCreated | MessageCallback) -> int | None:
    if isinstance(ev, MessageCallback):
        return ev.callback.user.user_id
    return user_id_from_message(ev)


class TemporaryPassStates(StatesGroup):
    AWAIT_EDIT_DESTINATION = State()
    AWAIT_REJECT_COMMENT = State()
    EDITING_PASS = State()
    AWAIT_EDIT_CAR_BRAND = State()
    AWAIT_EDIT_CAR_MODEL = State()
    AWAIT_EDIT_CAR_NUMBER = State()
    AWAIT_EDIT_CARGO_TYPE = State()
    AWAIT_EDIT_PURPOSE = State()
    AWAIT_EDIT_VISIT_DATE = State()
    AWAIT_EDIT_COMMENT = State()
    AWAIT_EDIT_SECURITY_COMMENT = State()


def get_temporary_passes_management():
    return inline_kb([
        [CallbackButton(text="На подтверждении", payload="pending_temporary_passes")],
        [CallbackButton(text="Подтвержденные", payload="approved_temporary_passes")],
        [CallbackButton(text="Отклоненные", payload="rejected_temporary_passes")],
        [CallbackButton(text="⬅️ Назад", payload="back_to_passes")]
    ])


@router.message_callback(F.callback.payload == "temporary_passes_menu")
async def temporary_passes_menu(event: MessageCallback, context: BaseContext):
    try:
        await context.clear()
        await edit_or_send_callback(bot, event, "Управление временными пропусками:", get_temporary_passes_management())
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "back_to_passes")
async def back_to_passes(event: MessageCallback):
    try:
        await passes_menu(event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


async def show_temporary_passes(ev: MessageCreated | MessageCallback, context: BaseContext, status: str):
    try:
        data = await context.get_data()
        current_page = data.get('temp_pass_current_page', 0)

        async with AsyncSessionLocal() as session:
            # Получаем общее количество заявок
            total_count = await session.scalar(
                select(func.count(TemporaryPass.id))
                .where(TemporaryPass.status == status)
            )

            # Получаем заявки для текущей страницы
            result = await session.execute(
                select(TemporaryPass, Resident.fio, Contractor.fio)
                .outerjoin(Resident, Resident.id == TemporaryPass.resident_id)
                .outerjoin(Contractor, Contractor.id == TemporaryPass.contractor_id)
                .where(TemporaryPass.status == status)
                .order_by(TemporaryPass.created_at.desc())
                .offset(current_page * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
            requests = result.all()

        if not requests:
            text = f"Нет пропусков со статусом '{status}'"
            back_kb = inline_kb(
                [[CallbackButton(text="⬅️ Назад", payload="temporary_passes_menu")]]
            )
            if isinstance(ev, MessageCallback):
                await edit_or_send_callback(bot, ev, text, back_kb)
            else:
                await answer_message(ev, text, kb=back_kb)
            return

        # Формируем кнопки
        buttons = []
        for req, res_fio, con_fio in requests:
            owner_name = res_fio or con_fio or "Представитель УК Eli Estate"
            fio_short = ' '.join(owner_name.split()[:2])
            btn_text = f"{fio_short}_{req.car_number}"
            buttons.append(
                [CallbackButton(text=btn_text, payload=f"view_temp_pass_{req.id}")]
            )

        # Добавляем кнопки пагинации
        pagination_buttons = []
        if current_page > 0:
            pagination_buttons.append(
                CallbackButton(text="⬅️ Предыдущие", payload=f"temp_pass_prev_{current_page - 1}_{status}")
            )

        if (current_page + 1) * PAGE_SIZE < total_count:
            pagination_buttons.append(
                CallbackButton(text="Следующие ➡️", payload=f"temp_pass_next_{current_page + 1}_{status}")
            )

        if pagination_buttons:
            buttons.append(pagination_buttons)

        buttons.append(
            [CallbackButton(text="⬅️ Назад", payload="temporary_passes_menu")]
        )

        status_text = {
            'pending': "На подтверждении",
            'approved': "Подтвержденные",
            'rejected': "Отклоненные"
        }[status]

        text = f"Временные пропуска ({status_text}):"
        if isinstance(ev, MessageCallback):
            await edit_or_send_callback(bot, ev, text, inline_kb(buttons))
        else:
            await answer_message(ev, text, kb=inline_kb(buttons))

        await context.update_data(
            temp_pass_current_page=current_page,
            temp_pass_total_count=total_count,
            temp_pass_status=status
        )
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{_uid(ev)} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload.startswith("pending_temporary_passes"))
async def show_pending_passes(event: MessageCallback, context: BaseContext):
    try:
        await show_temporary_passes(event, context, 'pending')
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload.startswith("approved_temporary_passes"))
async def show_approved_passes(event: MessageCallback, context: BaseContext):
    try:
        await show_temporary_passes(event, context, 'approved')
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload.startswith("rejected_temporary_passes"))
async def show_rejected_passes(event: MessageCallback, context: BaseContext):
    try:
        await show_temporary_passes(event, context, 'rejected')
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload.startswith("temp_pass_prev_") | F.callback.payload.startswith("temp_pass_next_"))
async def handle_temp_pass_pagination(event: MessageCallback, context: BaseContext):
    try:
        parts = event.callback.payload.split("_")
        action = parts[2]
        page = int(parts[3])
        status = parts[4]
        await context.update_data(temp_pass_current_page=page)
        await show_temporary_passes(event, context, status)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


async def get_pass_owner_info(session, pass_request):
    if pass_request.owner_type == "resident":
        resident = await session.get(Resident, pass_request.resident_id)
        if not resident:
            return "Резидент не найден"
        plink = profile_link_line_html(
            resident.first_name,
            resident.last_name,
            resident.tg_id,
            fallback_fio=resident.fio,
        ).rstrip("\n")
        return f"Резидент: {fio_html(resident.fio, resident.tg_id)}\n{plink}"
    elif pass_request.owner_type == "contractor":
        contractor = await session.get(Contractor, pass_request.contractor_id)
        if not contractor:
            return "Подрядчик не найден"
        plink = profile_link_line_html(
            contractor.first_name,
            contractor.last_name,
            contractor.tg_id,
            fallback_fio=contractor.fio,
        ).rstrip("\n")
        return f"Подрядчик: {fio_html(contractor.fio, contractor.tg_id)}\n{plink}"
    else:
        return "Представитель УК"


async def get_temp_pass_payer_max_user_id(session, pass_request) -> int | None:
    """MAX user_id владельца заявки (для отображения спецтарифа)."""
    if pass_request.owner_type == "resident" and pass_request.resident_id:
        resident = await session.get(Resident, pass_request.resident_id)
        if resident and resident.tg_id is not None:
            return int(resident.tg_id)
    elif pass_request.owner_type == "contractor" and pass_request.contractor_id:
        contractor = await session.get(Contractor, pass_request.contractor_id)
        if contractor and contractor.tg_id is not None:
            return int(contractor.tg_id)
    return None


@router.message_callback(F.callback.payload.startswith("view_temp_pass_"))
async def view_temp_pass_details(event: MessageCallback, context: BaseContext):
    try:
        pass_id = int(event.callback.payload.split("_")[-1])
        await context.update_data(current_temp_pass_id=pass_id)

        async with AsyncSessionLocal() as session:
            pass_request = await session.get(TemporaryPass, pass_id)
            if not pass_request:
                await callback_ack(bot, event, "Пропуск не найден")
                return

            owner_info = await get_pass_owner_info(session, pass_request)
            payer_uid = await get_temp_pass_payer_max_user_id(session, pass_request)
            if is_new_truck_pass(pass_request):
                text = (
                    f"{owner_info}\n"
                    f"Тип ТС: Грузовой\n"
                    f"{new_truck_vehicle_block_html(pass_request)}"
                    f"{new_truck_price_line_html(pass_request, payer_uid)}"
                    f"Время создания: {pass_request.created_at.strftime('%d.%m.%Y %H:%M')}"
                )
            else:
                if pass_request.purpose in ['6', '13', '29']:
                    value = f'{int(pass_request.purpose) + 1} дней\n'
                elif pass_request.purpose == '1':
                    value = '2 дня\n'
                else:
                    value = '1 день\n'
                text = (
                    f"{owner_info}\n"
                    f"Тип ТС: {'Легковой' if pass_request.vehicle_type == 'car' else 'Грузовой'}\n"
                    f"Категория веса: {html_lib.escape(str(pass_request.weight_category or 'Н/Д'))}\n"
                    f"Категория длины: {html_lib.escape(str(pass_request.length_category or 'Н/Д'))}\n"
                    f"Тип груза: {html_lib.escape(str(pass_request.cargo_type or 'Н/Д'))}\n"
                    f"Номер: {html_lib.escape(pass_request.car_number)}\n"
                    f"Марка: {html_lib.escape(pass_request.car_brand)}\n"
                    f"Пункт назначения: {html_lib.escape(str(pass_request.destination or ''))}\n"
                    f"Дата визита: {pass_request.visit_date.strftime('%d.%m.%Y')}\n"
                    f"Действие пропуска: {value}"
                    f"Комментарий владельца: {html_lib.escape(str(pass_request.owner_comment or 'нет'))}\n"
                    f"Комментарий для СБ: {html_lib.escape(str(pass_request.security_comment or 'нет'))}\n"
                    f"Время создания: {pass_request.created_at.strftime('%d.%m.%Y %H:%M')}"
                )

            if pass_request.time_registration:
                text += f"\nВремя обработки: {pass_request.time_registration.strftime('%d.%m.%Y %H:%M')}"

            # Формируем клавиатуру действий
            keyboard_buttons = []
            if pass_request.status == 'pending':
                keyboard_buttons.extend([
                    [CallbackButton(text="✅ Одобрить", payload=f"approve_temp_pass_{pass_id}")],
                    [CallbackButton(text="✏️ Редактировать", payload="edit_temp_pass")],
                    [CallbackButton(text="❌ Отклонить", payload="reject_temp_pass")]
                ])

            keyboard_buttons.append(
                [CallbackButton(text="⬅️ Назад", payload="back_to_temp_passes_list")]
            )

            keyboard = inline_kb(keyboard_buttons)

            await edit_or_send_callback(bot, event, text, keyboard, parse_mode=ParseMode.HTML)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "back_to_temp_passes_list")
async def back_to_temp_passes_list(event: MessageCallback, context: BaseContext):
    try:
        data = await context.get_data()
        status = data.get('temp_pass_status', 'pending')
        await show_temporary_passes(event, context, status)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload.startswith("approve_temp_pass_"))
async def approve_temp_pass(event: MessageCallback, context: BaseContext):
    try:
        pass_id = int(event.callback.payload.split("_")[-1])

        async with AsyncSessionLocal() as session:
            pass_request = await session.get(TemporaryPass, pass_id)
            if not pass_request:
                await callback_ack(bot, event, "Пропуск не найден")
                return

            pass_request.status = 'approved'
            pass_request.time_registration = datetime.datetime.now()
            await session.commit()

            # Отправляем сообщение владельцу
            text_to_all = ''
            try:
                owner_id = None
                if pass_request.owner_type == "resident" and pass_request.resident_id:
                    resident = await session.get(Resident, pass_request.resident_id)
                    owner_id = resident.tg_id
                    text_to_all = f"от резидента {fio_html(resident.fio, resident.tg_id)}"
                elif pass_request.owner_type == "contractor" and pass_request.contractor_id:
                    contractor = await session.get(Contractor, pass_request.contractor_id)
                    owner_id = contractor.tg_id
                    text_to_all = (
                        "от подрядчика "
                        f"{html_lib.escape(str(contractor.company or ''))}, "
                        f"{html_lib.escape(str(contractor.position or ''))}, "
                        f"{fio_html(contractor.fio, contractor.tg_id)}"
                    )

                if owner_id:
                    await bot.send_message(
                        user_id=owner_id,
                        text=(
                            f"✅ Ваш временный пропуск на машину {pass_request.car_brand} "
                            f"{pass_request.car_number} одобрен!\n"
                            f"Дата визита: {pass_request.visit_date.strftime('%d.%m.%Y')}"
                        ),
                    )
                    await bot.send_message(user_id=owner_id, text=text_warning)
            except Exception as e:
                logging.error(f"Не удалось отправить сообщение владельцу: {e}")

            tg_ids = await get_active_admins_managers_sb_tg_ids()

            for tg_id in tg_ids:
                try:
                    await bot.send_message(
                        user_id=tg_id,
                        text=(
                            f"Временный пропуск {text_to_all} на машину с номером "
                            f"{html_lib.escape(pass_request.car_number)} одобрен."
                        ),
                        parse_mode=ParseMode.HTML,
                        attachments=[main_menu_inline_button_kb().as_markup()],
                    )
                    await asyncio.sleep(0.05)
                except:
                    pass
            await edit_or_send_callback(
                bot,
                event,
                "Управление временными пропусками:",
                get_temporary_passes_management(),
            )

    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "reject_temp_pass")
async def start_reject_temp_pass(event: MessageCallback, context: BaseContext):
    try:
        await event.message.answer("Введите комментарий для владельца пропуска:")
        await context.set_state(TemporaryPassStates.AWAIT_REJECT_COMMENT)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, TemporaryPassStates.AWAIT_REJECT_COMMENT)
async def process_temp_reject_comment(event: MessageCreated, context: BaseContext):
    try:
        data = await context.get_data()
        pass_id = data.get('current_temp_pass_id')

        if not pass_id:
            await answer_message(event, "Ошибка: ID пропуска не найден")
            await context.clear()
            return

        async with AsyncSessionLocal() as session:
            pass_request = await session.get(TemporaryPass, pass_id)
            if not pass_request:
                await answer_message(event, "Пропуск не найден")
                await context.clear()
                return

            pass_request.status = 'rejected'
            pass_request.time_registration = datetime.datetime.now()
            pass_request.resident_comment = (text_from_message(event) or '')
            await session.commit()

            # Отправляем сообщение владельцу
            try:
                owner_id = None
                if pass_request.owner_type == "resident" and pass_request.resident_id:
                    resident = await session.get(Resident, pass_request.resident_id)
                    owner_id = resident.tg_id
                elif pass_request.owner_type == "contractor" and pass_request.contractor_id:
                    contractor = await session.get(Contractor, pass_request.contractor_id)
                    owner_id = contractor.tg_id

                if owner_id:
                    await bot.send_message(
                        owner_id,
                        f"❌ Ваш временный пропуск на машину {pass_request.car_number} отклонен.\n"
                        f"Причина: {(text_from_message(event) or '')}"
                    )
            except Exception as e:
                logging.error(f"Не удалось отправить сообщение владельцу: {e}")

            await answer_message(event, "Пропуск отклонен")
            await answer_message(event, 
                "Управление временными пропусками:",
                kb=get_temporary_passes_management()
            )

        # Возвращаем админа в список
        data = await context.get_data()
        status = data.get('temp_pass_status', 'pending')
        await show_temporary_passes(event, context, status)
        await context.clear()
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


def get_temp_edit_pass_keyboard():
    return inline_kb([
        [
            CallbackButton(text="Марка", payload="edit_temp_car_brand"),
            CallbackButton(text="Номер", payload="edit_temp_car_number"),
        ],
        [
            CallbackButton(text="Тип груза", payload="edit_temp_cargo_type"),
            CallbackButton(text="Дата визита", payload="edit_temp_visit_date"),
        ],
        [
            CallbackButton(text="Коммент. владельца", payload="edit_temp_comment"),
            CallbackButton(text="Пункт назначения", payload="edit_temp_destination"),
        ],
        [
            CallbackButton(text="Коммент. для СБ", payload="edit_temp_security_comment"),
            CallbackButton(text="✅ Готово", payload="edit_temp_finish_pass"),
        ],
    ])


@router.message_callback(F.callback.payload == "edit_temp_pass")
async def start_editing_temp_pass(event: MessageCallback, context: BaseContext):
    try:
        cur = (
            (event.message.body.text if event.message and event.message.body else None)
            or "Выберите поле для редактирования:"
        )
        await edit_or_send_callback(bot, event, cur, get_temp_edit_pass_keyboard())
        await context.set_state(TemporaryPassStates.EDITING_PASS)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "edit_temp_finish_pass", TemporaryPassStates.EDITING_PASS)
async def finish_editing_temp_pass(event: MessageCallback, context: BaseContext):
    try:
        data = await context.get_data()
        pass_id = data.get('current_temp_pass_id')

        async with AsyncSessionLocal() as session:
            pass_request = await session.get(TemporaryPass, pass_id)
            owner_info = await get_pass_owner_info(session, pass_request)
            payer_uid = await get_temp_pass_payer_max_user_id(session, pass_request)
            if is_new_truck_pass(pass_request):
                text = (
                    f"{owner_info}\n"
                    f"Тип ТС: Грузовой\n"
                    f"{new_truck_vehicle_block_html(pass_request)}"
                    f"{new_truck_price_line_html(pass_request, payer_uid)}"
                )
            else:
                if pass_request.purpose in ['6', '13', '29']:
                    value = f'{int(pass_request.purpose) + 1} дней\n'
                elif pass_request.purpose == '1':
                    value = '2 дня\n'
                else:
                    value = '1 день\n'
                text = (
                    f"{owner_info}\n"
                    f"Тип ТС: {'Легковой' if pass_request.vehicle_type == 'car' else 'Грузовой'}\n"
                    f"Номер: {pass_request.car_number}\n"
                    f"Марка: {pass_request.car_brand}\n"
                    f"Тип груза: {pass_request.cargo_type}\n"
                    f"Пункт назначения: {pass_request.destination}\n"
                    f"Дата визита: {pass_request.visit_date.strftime('%d.%m.%Y')}\n"
                    f"Действие пропуска: {value}"
                    f"Комментарий владельца: {pass_request.owner_comment or 'нет'}\n"
                    f"Комментарий для СБ: {pass_request.security_comment or 'нет'}"
                )

            keyboard_buttons = []
            if pass_request.status == 'pending':
                keyboard_buttons.extend([
                    [CallbackButton(text="✅ Одобрить", payload=f"approve_temp_pass_{pass_id}")],
                    [CallbackButton(text="✏️ Редактировать", payload="edit_temp_pass")],
                    [CallbackButton(text="❌ Отклонить", payload="reject_temp_pass")]
                ])

            keyboard_buttons.append(
                [CallbackButton(text="⬅️ Назад", payload="back_to_temp_passes_list")]
            )

            keyboard = inline_kb(keyboard_buttons)

            await edit_or_send_callback(bot, event, text, keyboard)

        await context.set_state(None)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload.startswith("edit_temp_"), TemporaryPassStates.EDITING_PASS)
async def handle_edit_temp_pass_actions(event: MessageCallback, context: BaseContext):
    try:
        action = event.callback.payload.replace("edit_temp_", "")

        if action == "car_brand":
            await event.message.answer("Введите новую марку машины:")
            await context.set_state(TemporaryPassStates.AWAIT_EDIT_CAR_BRAND)
        elif action == "car_number":
            await event.message.answer("Введите новый номер машины:")
            await context.set_state(TemporaryPassStates.AWAIT_EDIT_CAR_NUMBER)
        elif action == "cargo_type":
            await event.message.answer("Введите новый тип груза:")
            await context.set_state(TemporaryPassStates.AWAIT_EDIT_CARGO_TYPE)
        elif action == "destination":
            await event.message.answer("Введите новую пункт назначения(номер участка):")
            await context.set_state(TemporaryPassStates.AWAIT_EDIT_DESTINATION)
        # elif action == "purpose":
        #     await event.message.answer("Введите новую цель визита:")
        #     await context.set_state(TemporaryPassStates.AWAIT_EDIT_PURPOSE)
        elif action == "visit_date":
            await event.message.answer("Введите новую дату визита (в формате ДД.ММ, ДД.ММ.ГГГГ или например '5 июня'):")
            await context.set_state(TemporaryPassStates.AWAIT_EDIT_VISIT_DATE)
        elif action == "comment":
            await event.message.answer("Введите новый комментарий владельца:")
            await context.set_state(TemporaryPassStates.AWAIT_EDIT_COMMENT)
        elif action == "security_comment":
            await event.message.answer("Введите новый комментарий для СБ:")
            await context.set_state(TemporaryPassStates.AWAIT_EDIT_SECURITY_COMMENT)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


# Обновление марки машины
@router.message_created(F.message.body.text, TemporaryPassStates.AWAIT_EDIT_CAR_BRAND)
async def update_temp_car_brand(event: MessageCreated, context: BaseContext):
    try:
        data = await context.get_data()
        pass_id = data.get('current_temp_pass_id')

        async with AsyncSessionLocal() as session:
            pass_request = await session.get(TemporaryPass, pass_id)
            pass_request.car_brand = (text_from_message(event) or '')
            await session.commit()
            await update_temp_pass_view(event, pass_request, session)
        await context.set_state(TemporaryPassStates.EDITING_PASS)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


# Обновление номера машины
@router.message_created(F.message.body.text, TemporaryPassStates.AWAIT_EDIT_CAR_NUMBER)
async def update_temp_car_number(event: MessageCreated, context: BaseContext):
    try:
        data = await context.get_data()
        pass_id = data.get('current_temp_pass_id')

        async with AsyncSessionLocal() as session:
            pass_request = await session.get(TemporaryPass, pass_id)
            pass_request.car_number = (text_from_message(event) or '').upper().strip()
            await session.commit()
            await update_temp_pass_view(event, pass_request, session)
        await context.set_state(TemporaryPassStates.EDITING_PASS)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


# Обновление типа груза
@router.message_created(F.message.body.text, TemporaryPassStates.AWAIT_EDIT_CARGO_TYPE)
async def update_temp_cargo_type(event: MessageCreated, context: BaseContext):
    try:
        data = await context.get_data()
        pass_id = data.get('current_temp_pass_id')

        async with AsyncSessionLocal() as session:
            pass_request = await session.get(TemporaryPass, pass_id)
            pass_request.cargo_type = (text_from_message(event) or '')
            await session.commit()
            await update_temp_pass_view(event, pass_request, session)
        await context.set_state(TemporaryPassStates.EDITING_PASS)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, TemporaryPassStates.AWAIT_EDIT_DESTINATION)
async def update_temp_purpose(event: MessageCreated, context: BaseContext):
    try:
        data = await context.get_data()
        pass_id = data.get('current_temp_pass_id')

        async with AsyncSessionLocal() as session:
            pass_request = await session.get(TemporaryPass, pass_id)
            pass_request.destination = (text_from_message(event) or '')
            await session.commit()
            await update_temp_pass_view(event, pass_request, session)
        await context.set_state(TemporaryPassStates.EDITING_PASS)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


# Обновление цели визита
# @router.message_created(F.message.body.text, TemporaryPassStates.AWAIT_EDIT_PURPOSE)
# async def update_temp_purpose(event: MessageCreated, context: BaseContext):
#     try:
#         data = await context.get_data()
#         pass_id = data.get('current_temp_pass_id')
#
#         async with AsyncSessionLocal() as session:
#             pass_request = await session.get(TemporaryPass, pass_id)
#             pass_request.purpose = (text_from_message(event) or '')
#             await session.commit()
#             await update_temp_pass_view(event, pass_request, session)
#         await context.set_state(TemporaryPassStates.EDITING_PASS)
#     except Exception as e:
#         await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
#         await asyncio.sleep(0.05)


# Обновление даты визита
@router.message_created(F.message.body.text, TemporaryPassStates.AWAIT_EDIT_VISIT_DATE)
async def update_temp_visit_date(event: MessageCreated, context: BaseContext):
    try:
        user_input = (text_from_message(event) or '').strip()
        visit_date = parse_date(user_input)
        now = datetime.datetime.now().date()

        if not visit_date:
            await answer_message(event, "❌ Неверный формат даты! Введите в формате ДД.ММ, ДД.ММ.ГГГГ или например '5 июня'")
            return

        if visit_date < now:
            await answer_message(event, "Дата не может быть меньше текущей даты. Введите снова:")
            return

        max_date = now + datetime.timedelta(days=31)
        if visit_date > max_date:
            await answer_message(event, "Пропуск нельзя заказать на месяц вперед. Введите снова:")
            return
        data = await context.get_data()
        pass_id = data.get('current_temp_pass_id')

        async with AsyncSessionLocal() as session:
            pass_request = await session.get(TemporaryPass, pass_id)
            pass_request.visit_date = visit_date
            await session.commit()
            await update_temp_pass_view(event, pass_request, session)
        await context.set_state(TemporaryPassStates.EDITING_PASS)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


# Обновление комментария владельца
@router.message_created(F.message.body.text, TemporaryPassStates.AWAIT_EDIT_COMMENT)
async def update_temp_comment(event: MessageCreated, context: BaseContext):
    try:
        data = await context.get_data()
        pass_id = data.get('current_temp_pass_id')

        async with AsyncSessionLocal() as session:
            pass_request = await session.get(TemporaryPass, pass_id)
            pass_request.owner_comment = (text_from_message(event) or '')
            await session.commit()
            await update_temp_pass_view(event, pass_request, session)
        await context.set_state(TemporaryPassStates.EDITING_PASS)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


# Обновление комментария для СБ
@router.message_created(F.message.body.text, TemporaryPassStates.AWAIT_EDIT_SECURITY_COMMENT)
async def update_temp_security_comment(event: MessageCreated, context: BaseContext):
    try:
        data = await context.get_data()
        pass_id = data.get('current_temp_pass_id')

        async with AsyncSessionLocal() as session:
            pass_request = await session.get(TemporaryPass, pass_id)
            pass_request.security_comment = (text_from_message(event) or '')
            await session.commit()
            await update_temp_pass_view(event, pass_request, session)
        await context.set_state(TemporaryPassStates.EDITING_PASS)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


async def update_temp_pass_view(event: MessageCreated, pass_request, session):
    owner_info = await get_pass_owner_info(session, pass_request)
    payer_uid = await get_temp_pass_payer_max_user_id(session, pass_request)
    if is_new_truck_pass(pass_request):
        text = (
            f"{owner_info}\n"
            f"Тип ТС: Грузовой\n"
            f"{new_truck_vehicle_block_html(pass_request)}"
            f"{new_truck_price_line_html(pass_request, payer_uid)}"
        )
    else:
        if pass_request.purpose in ['6', '13', '29']:
            value = f'{int(pass_request.purpose) + 1} дней\n'
        elif pass_request.purpose == '1':
            value = '2 дня\n'
        else:
            value = '1 день\n'
        text = (
            f"{owner_info}\n"
            f"Тип ТС: {'Легковой' if pass_request.vehicle_type == 'car' else 'Грузовой'}\n"
            f"Номер: {pass_request.car_number}\n"
            f"Марка: {pass_request.car_brand}\n"
            f"Тип груза: {pass_request.cargo_type}\n"
            f"Пункт назначения: {pass_request.destination}\n"
            f"Дата визита: {pass_request.visit_date.strftime('%d.%m.%Y')}\n"
            f"Действие пропуска: {value}"
            f"Комментарий владельца: {pass_request.owner_comment or 'нет'}\n"
            f"Комментарий для СБ: {pass_request.security_comment or 'нет'}"
        )
    await answer_message(event, text, kb=get_temp_edit_pass_keyboard())


@router.message_created(Command("delete_temporary"), IsAdminOrManager())
async def delete_old_temporary_passes(event: MessageCreated):
    if user_id_from_message(event) != RAZRAB:
        return
    """Удаление временных пропусков старше 30 дней"""
    try:
        cutoff_date = datetime.datetime.now().date() - datetime.timedelta(days=30)

        async with AsyncSessionLocal() as session:
            # Удаляем временные пропуска
            result = await session.execute(
                delete(TemporaryPass).where(TemporaryPass.created_at <= cutoff_date)
            )
            deleted_count = result.rowcount

            await session.commit()

        await answer_message(event, 
            f"✅ Удалено {deleted_count} временных пропусков за период до {cutoff_date.strftime('%d.%m.%Y')}",
                    )

    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await answer_message(event, "❌ Произошла ошибка при удалении пропусков")