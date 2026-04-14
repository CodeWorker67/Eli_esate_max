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
from sqlalchemy import select, func

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
from db.models import AsyncSessionLocal, Resident, PermanentPass
from config import PAGE_SIZE, RAZRAB
from db.util import get_active_admins_managers_sb_tg_ids
from filters import IsAdminOrManager

router = Router(router_id="admin_permanent_pass")
router.filter(IsAdminOrManager())


class PermanentPassStates(StatesGroup):
    AWAIT_EDIT_DESTINATION = State()
    AWAIT_REJECT_COMMENT = State()
    EDITING_PASS = State()
    AWAIT_EDIT_CAR_BRAND = State()
    AWAIT_EDIT_CAR_MODEL = State()
    AWAIT_EDIT_CAR_NUMBER = State()
    AWAIT_EDIT_CAR_OWNER = State()
    AWAIT_EDIT_SECURITY_COMMENT = State()


def get_passes_menu():
    return inline_kb([
        [CallbackButton(text="Постоянные пропуска", payload="permanent_passes_menu")],
        [CallbackButton(text="Временные пропуска", payload="temporary_passes_menu")],
        [CallbackButton(text="Выписать временный пропуск", payload="issue_self_pass")],
        [CallbackButton(text="Выписать постоянный пропуск", payload="issue_permanent_self_pass")],
        [CallbackButton(text="⬅️ Назад", payload="back_to_main")]
    ])


def get_permanent_passes_management():
    return inline_kb([
        [CallbackButton(text="На подтверждении", payload="pending_permanent_passes")],
        [CallbackButton(text="Подтвержденные", payload="approved_permanent_passes")],
        [CallbackButton(text="Отклоненные", payload="rejected_permanent_passes")],
        [CallbackButton(text="⬅️ Назад", payload="back_to_passes")]
    ])


@router.message_callback(F.callback.payload == "passes_menu")
async def passes_menu(event: MessageCallback):
    try:
        await edit_or_send_callback(bot, event, "Пропуска:", get_passes_menu())
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "permanent_passes_menu")
async def permanent_passes_menu(event: MessageCallback, context: BaseContext):
    try:
        await context.clear()
        await edit_or_send_callback(bot, event, "Управление постоянными пропусками:", get_permanent_passes_management())
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


@router.message_callback(F.callback.payload == "pending_permanent_passes")
async def show_pending_passes(event: MessageCallback, context: BaseContext):
    try:

        data = await context.get_data()
        current_page = data.get('pass_current_page', 0)

        async with AsyncSessionLocal() as session:
            # Получаем общее количество заявок
            total_count = await session.scalar(
                select(func.count(PermanentPass.id))
                .where(PermanentPass.status == 'pending')
            )

            # Получаем заявки для текущей страницы
            result = await session.execute(
                select(
                    PermanentPass,
                    Resident.fio,
                    Resident.tg_id,
                    Resident.first_name,
                    Resident.last_name,
                )
                .join(Resident, Resident.id == PermanentPass.resident_id)
                .where(PermanentPass.status == 'pending')
                .order_by(PermanentPass.created_at.desc())
                .offset(current_page * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
            requests = result.all()

        if not requests:
            await edit_or_send_callback(
                bot, event, "Нет пропусков на подтверждении", None
            )
            return

        # Формируем кнопки
        buttons = []
        for req, fio, res_tg_id, res_fn, res_ln in requests:
            # Берем первые два слова из ФИО
            fio_short = ' '.join(fio.split()[:2])
            text = f"{fio_short}_{req.car_number}"
            buttons.append(
                [CallbackButton(text=text, payload=f"view_pass_{req.id}")]
            )

        # Добавляем кнопки пагинации
        pagination_buttons = []
        if current_page > 0:
            pagination_buttons.append(
                CallbackButton(text="⬅️ Предыдущие", payload=f"pass_prev_{current_page - 1}")
            )

        if (current_page + 1) * PAGE_SIZE < total_count:
            pagination_buttons.append(
                CallbackButton(text="Следующие ➡️", payload=f"pass_next_{current_page + 1}")
            )

        if pagination_buttons:
            buttons.append(pagination_buttons)

        buttons.append(
            [CallbackButton(text="⬅️ Назад", payload="permanent_passes_menu")]
        )

        list_text = "Пропуска требуют подтверждения:"
        await edit_or_send_callback(bot, event, list_text, inline_kb(buttons))

        await context.update_data(
            pass_current_page=current_page,
            pass_total_count=total_count
        )
    except Exception as e:
        await bot.send_message(
            user_id=RAZRAB,
            text=f'{event.callback.user.user_id} - {str(e)}',
        )
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload.startswith("pass_prev_") | F.callback.payload.startswith("pass_next_"))
async def handle_pending_pass_pagination(event: MessageCallback, context: BaseContext):
    try:
        page = int(event.callback.payload.rsplit("_", 1)[-1])
        await context.update_data(pass_current_page=page)
        await show_pending_passes(event, context)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload.startswith("view_pass_"))
async def view_pass_details(event: MessageCallback, context: BaseContext):
    try:
        pass_id = int(event.callback.payload.split("_")[-1])
        await context.update_data(current_pass_id=pass_id)

        async with AsyncSessionLocal() as session:
            # Получаем пропуск и связанного резидента
            result = await session.execute(
                select(
                    PermanentPass,
                    Resident.fio,
                    Resident.tg_id,
                    Resident.first_name,
                    Resident.last_name,
                )
                .join(Resident, Resident.id == PermanentPass.resident_id)
                .where(PermanentPass.id == pass_id)
            )
            pass_request, fio, res_tg_id, res_fn, res_ln = result.first()

            if not pass_request:
                await callback_ack(bot, event, "Пропуск не найден")
                return

            # Формируем текст сообщения
            text = (
                f"ФИО: {fio_html(fio, res_tg_id)}\n"
                f"{profile_link_line_html(res_fn, res_ln, res_tg_id, fallback_fio=fio)}"
                f"Марка: {pass_request.car_brand}\n"
                f"Модель: {pass_request.car_model}\n"
                f"Номер: {pass_request.car_number}\n"
                f"Владелец: {pass_request.car_owner}\n"
                f"Пункт назначения: {pass_request.destination}\n"
                f"Комментарий для СБ: {pass_request.security_comment or 'нет'}\n"
                f"Время создания: {pass_request.created_at.strftime('%d.%m.%Y %H:%M')}"
            )

            # Формируем клавиатуру действий (добавляем кнопку Редактировать)
            keyboard = inline_kb([
                [CallbackButton(text="✅ Одобрить", payload=f"approve_pass_{pass_id}")],
                [CallbackButton(text="✏️ Редактировать", payload="edit_pass")],
                [CallbackButton(text="❌ Отклонить", payload="reject_pass")],
                [CallbackButton(text="⬅️ Назад", payload="back_to_pending_passes")]
            ])

            await edit_or_send_callback(bot, event, text, keyboard, parse_mode=ParseMode.HTML)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "back_to_pending_passes")
async def back_to_pending_list(event: MessageCallback, context: BaseContext):
    try:
        await show_pending_passes(event, context)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload.startswith("approve_pass_"))
async def approve_pass(event: MessageCallback, context: BaseContext):
    try:
        pass_id = int(event.callback.payload.split("_")[-1])

        async with AsyncSessionLocal() as session:
            # Получаем пропуск
            pass_request = await session.get(PermanentPass, pass_id)
            if not pass_request:
                await callback_ack(bot, event, "Пропуск не найден")
                return

            # Обновляем статус и время
            pass_request.status = 'approved'
            pass_request.time_registration = datetime.datetime.now()
            await session.commit()

            # Получаем резидента для отправки сообщения
            resident = await session.get(Resident, pass_request.resident_id)

            # Отправляем сообщение резиденту
            try:
                await bot.send_message(
                    user_id=resident.tg_id,
                    text=(
                        f"✅ Ваш постоянный пропуск на машину с номером "
                        f"{pass_request.car_number} одобрен!"
                    ),
                )
            except Exception as e:
                logging.error(f"Не удалось отправить сообщение резиденту: {e}")
            tg_ids = await get_active_admins_managers_sb_tg_ids()
            for tg_id in tg_ids:
                try:
                    await bot.send_message(
                        user_id=tg_id,
                        text=(
                            f"Постоянный пропуск от резидента {fio_html(resident.fio, resident.tg_id)} "
                            f"на машину с номером {html_lib.escape(str(pass_request.car_number or ''))} одобрен."
                        ),
                        parse_mode=ParseMode.HTML,
                        attachments=[main_menu_inline_button_kb().as_markup()],
                    )
                    await asyncio.sleep(0.05)
                except:
                    pass
            # Сообщение админу
            await event.message.answer(
                text="Управление постоянными пропусками:",
                attachments=[get_permanent_passes_management().as_markup()],
            )
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "reject_pass")
async def start_reject_pass(event: MessageCallback, context: BaseContext):
    try:
        await event.message.answer("Введите комментарий для резидента:")
        await context.set_state(PermanentPassStates.AWAIT_REJECT_COMMENT)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, PermanentPassStates.AWAIT_REJECT_COMMENT)
async def process_reject_comment(event: MessageCreated, context: BaseContext):
    try:
        data = await context.get_data()
        pass_id = data.get('current_pass_id')

        if not pass_id:
            await answer_message(event, "Ошибка: ID пропуска не найден")
            await context.clear()
            return

        async with AsyncSessionLocal() as session:
            # Получаем пропуск
            pass_request = await session.get(PermanentPass, pass_id)
            if not pass_request:
                await answer_message(event, "Пропуск не найден")
                await context.clear()
                return

            # Обновляем статус и комментарий
            pass_request.status = 'rejected'
            pass_request.time_registration = datetime.datetime.now()
            pass_request.resident_comment = (text_from_message(event) or '')
            await session.commit()

            # Получаем резидента для отправки сообщения
            resident = await session.get(Resident, pass_request.resident_id)

            # Отправляем сообщение резиденту
            try:
                await bot.send_message(
                    user_id=resident.tg_id,
                    text=(
                        f"❌ Ваша заявка на постоянный пропуск для машины с номером "
                        f"{pass_request.car_number} отклонена.\n"
                        f"Причина: {(text_from_message(event) or '')}"
                    ),
                )
            except Exception as e:
                logging.error(f"Не удалось отправить сообщение резиденту: {e}")

            # Сообщение админу
            await answer_message(event, "❌ Заявка отклонена")
            await answer_message(
                event,
                "Управление постоянными пропусками:",
                kb=get_permanent_passes_management(),
            )

        await context.clear()
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


# Клавиатура для редактирования пропуска
def get_edit_pass_keyboard():
    return inline_kb([
        [
            CallbackButton(text="Марка", payload="edit_car_brand"),
            CallbackButton(text="Модель", payload="edit_car_model"),
        ],
        [
            CallbackButton(text="Номер", payload="edit_car_number"),
            CallbackButton(text="Владелец", payload="edit_car_owner"),
        ],
        [
            CallbackButton(text="Пункт назначения", payload="edit_car_destination"),
            CallbackButton(text="Коммент. для СБ", payload="edit_security_comment"),
        ],
        [
            CallbackButton(text="✅ Готово", payload="edit_finish_pass")
        ]
    ])


# Обработчик кнопки "Редактировать"
@router.message_callback(F.callback.payload == "edit_pass")
async def start_editing_pass(event: MessageCallback, context: BaseContext):
    try:
        b = event.message.body
        if b:
            await event.message.edit(
                text=b.text or "",
                attachments=[get_edit_pass_keyboard().as_markup()],
            )
        await context.set_state(PermanentPassStates.EDITING_PASS)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "edit_finish_pass", PermanentPassStates.EDITING_PASS)
async def finish_editing_pass(event: MessageCallback, context: BaseContext):
    try:
        data = await context.get_data()
        pass_id = data.get('current_pass_id')

        async with AsyncSessionLocal() as session:
            # Получаем пропуск и связанного резидента
            result = await session.execute(
                select(
                    PermanentPass,
                    Resident.fio,
                    Resident.tg_id,
                    Resident.first_name,
                    Resident.last_name,
                )
                .join(Resident, Resident.id == PermanentPass.resident_id)
                .where(PermanentPass.id == pass_id)
            )
            pass_request, fio, res_tg_id, res_fn, res_ln = result.first()

            # Формируем текст сообщения
            text = (
                f"ФИО: {fio_html(fio, res_tg_id)}\n"
                f"{profile_link_line_html(res_fn, res_ln, res_tg_id, fallback_fio=fio)}"
                f"Марка: {pass_request.car_brand}\n"
                f"Модель: {pass_request.car_model}\n"
                f"Номер: {pass_request.car_number}\n"
                f"Владелец: {pass_request.car_owner}\n"
                f"Пункт назначения: {pass_request.destination}\n"
                f"Комментарий для СБ: {pass_request.security_comment or 'нет'}\n"
                f"Время создания: {pass_request.created_at.strftime('%d.%m.%Y %H:%M')}"
            )

            # Формируем клавиатуру действий
            keyboard = inline_kb([
                [CallbackButton(text="✅ Одобрить", payload=f"approve_pass_{pass_id}")],
                [CallbackButton(text="✏️ Редактировать", payload="edit_pass")],
                [CallbackButton(text="❌ Отклонить", payload="reject_pass")],
                [CallbackButton(text="⬅️ Назад", payload="back_to_pending_passes")]
            ])

            await edit_or_send_callback(bot, event, text, keyboard, parse_mode=ParseMode.HTML)

        await context.set_state(None)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)



# Обработчики для кнопок редактирования
@router.message_callback(F.callback.payload.startswith("edit_"), PermanentPassStates.EDITING_PASS)
async def handle_edit_pass_actions(event: MessageCallback, context: BaseContext):
    try:
        action = event.callback.payload.split("_")[1] + '_' + event.callback.payload.split("_")[2]

        if action == "car_brand":
            await event.message.answer("Введите новую марку машины:")
            await context.set_state(PermanentPassStates.AWAIT_EDIT_CAR_BRAND)
        elif action == "car_model":
            await event.message.answer("Введите новую модель машины:")
            await context.set_state(PermanentPassStates.AWAIT_EDIT_CAR_MODEL)
        elif action == "car_number":
            await event.message.answer("Введите новый номер машины:")
            await context.set_state(PermanentPassStates.AWAIT_EDIT_CAR_NUMBER)
        elif action == "car_owner":
            await event.message.answer("Введите нового владельца машины:")
            await context.set_state(PermanentPassStates.AWAIT_EDIT_CAR_OWNER)
        elif action == "car_destination":
            await event.message.answer("Введите новый номер участка:")
            await context.set_state(PermanentPassStates.AWAIT_EDIT_DESTINATION)
        elif action == "security_comment":
            await event.message.answer("Введите новый комментарий для СБ:")
            await context.set_state(PermanentPassStates.AWAIT_EDIT_SECURITY_COMMENT)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


# Обновление марки машины
@router.message_created(F.message.body.text, PermanentPassStates.AWAIT_EDIT_CAR_BRAND)
async def update_car_brand(event: MessageCreated, context: BaseContext):
    try:
        data = await context.get_data()
        pass_id = data.get('current_pass_id')

        async with AsyncSessionLocal() as session:
            pass_request = await session.get(PermanentPass, pass_id)
            pass_request.car_brand = (text_from_message(event) or '')
            await session.commit()

            # Получаем обновленные данные
            result = await session.execute(
                select(
                    PermanentPass,
                    Resident.fio,
                    Resident.tg_id,
                    Resident.first_name,
                    Resident.last_name,
                )
                .join(Resident, Resident.id == PermanentPass.resident_id)
                .where(PermanentPass.id == pass_id)
            )
            pass_request, fio, res_tg_id, res_fn, res_ln = result.first()

            # Формируем текст сообщения
            text = (
                f"ФИО: {fio_html(fio, res_tg_id)}\n"
                f"{profile_link_line_html(res_fn, res_ln, res_tg_id, fallback_fio=fio)}"
                f"Марка: {(text_from_message(event) or '')}\n"
                f"Модель: {pass_request.car_model}\n"
                f"Номер: {pass_request.car_number}\n"
                f"Владелец: {pass_request.car_owner}\n"
                f"Пункт назначения: {pass_request.destination}\n"
                f"Комментарий для СБ: {pass_request.security_comment or 'нет'}\n"
                f"Время создания: {pass_request.created_at.strftime('%d.%m.%Y %H:%M')}"
            )

            # Отправляем обновленное сообщение
            await answer_message(
                event,
                text,
                kb=get_edit_pass_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        await context.set_state(PermanentPassStates.EDITING_PASS)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


# Обновление модели машины (аналогично для остальных полей)
@router.message_created(F.message.body.text, PermanentPassStates.AWAIT_EDIT_CAR_MODEL)
async def update_car_model(event: MessageCreated, context: BaseContext):
    try:
        data = await context.get_data()
        pass_id = data.get('current_pass_id')

        async with AsyncSessionLocal() as session:
            pass_request = await session.get(PermanentPass, pass_id)
            pass_request.car_model = (text_from_message(event) or '')
            await session.commit()

            result = await session.execute(
                select(
                    PermanentPass,
                    Resident.fio,
                    Resident.tg_id,
                    Resident.first_name,
                    Resident.last_name,
                )
                .join(Resident, Resident.id == PermanentPass.resident_id)
                .where(PermanentPass.id == pass_id)
            )
            pass_request, fio, res_tg_id, res_fn, res_ln = result.first()

            text = (
                f"ФИО: {fio_html(fio, res_tg_id)}\n"
                f"{profile_link_line_html(res_fn, res_ln, res_tg_id, fallback_fio=fio)}"
                f"Марка: {pass_request.car_brand}\n"
                f"Модель: {(text_from_message(event) or '')}\n"
                f"Номер: {pass_request.car_number}\n"
                f"Владелец: {pass_request.car_owner}\n"
                f"Пункт назначения: {pass_request.destination}\n"
                f"Комментарий для СБ: {pass_request.security_comment or 'нет'}\n"
                f"Время создания: {pass_request.created_at.strftime('%d.%m.%Y %H:%M')}"
            )

            await answer_message(
                event,
                text,
                kb=get_edit_pass_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        await context.set_state(PermanentPassStates.EDITING_PASS)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


# Обновление номера машины
@router.message_created(F.message.body.text, PermanentPassStates.AWAIT_EDIT_CAR_NUMBER)
async def update_car_number(event: MessageCreated, context: BaseContext):
    try:
        data = await context.get_data()
        pass_id = data.get('current_pass_id')

        async with AsyncSessionLocal() as session:
            pass_request = await session.get(PermanentPass, pass_id)
            pass_request.car_number = (text_from_message(event) or '').upper().strip()
            await session.commit()

            result = await session.execute(
                select(
                    PermanentPass,
                    Resident.fio,
                    Resident.tg_id,
                    Resident.first_name,
                    Resident.last_name,
                )
                .join(Resident, Resident.id == PermanentPass.resident_id)
                .where(PermanentPass.id == pass_id)
            )
            pass_request, fio, res_tg_id, res_fn, res_ln = result.first()

            text = (
                f"ФИО: {fio_html(fio, res_tg_id)}\n"
                f"{profile_link_line_html(res_fn, res_ln, res_tg_id, fallback_fio=fio)}"
                f"Марка: {pass_request.car_brand}\n"
                f"Модель: {pass_request.car_model}\n"
                f"Номер: {(text_from_message(event) or '')}\n"
                f"Владелец: {pass_request.car_owner}\n"
                f"Пункт назначения: {pass_request.destination}\n"
                f"Комментарий для СБ: {pass_request.security_comment or 'нет'}\n"
                f"Время создания: {pass_request.created_at.strftime('%d.%m.%Y %H:%M')}"
            )

            await answer_message(
                event,
                text,
                kb=get_edit_pass_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        await context.set_state(PermanentPassStates.EDITING_PASS)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


# Обновление владельца машины
@router.message_created(F.message.body.text, PermanentPassStates.AWAIT_EDIT_CAR_OWNER)
async def update_car_owner(event: MessageCreated, context: BaseContext):
    try:
        data = await context.get_data()
        pass_id = data.get('current_pass_id')

        async with AsyncSessionLocal() as session:
            pass_request = await session.get(PermanentPass, pass_id)
            pass_request.car_owner = (text_from_message(event) or '')
            await session.commit()

            result = await session.execute(
                select(
                    PermanentPass,
                    Resident.fio,
                    Resident.tg_id,
                    Resident.first_name,
                    Resident.last_name,
                )
                .join(Resident, Resident.id == PermanentPass.resident_id)
                .where(PermanentPass.id == pass_id)
            )
            pass_request, fio, res_tg_id, res_fn, res_ln = result.first()

            text = (
                f"ФИО: {fio_html(fio, res_tg_id)}\n"
                f"{profile_link_line_html(res_fn, res_ln, res_tg_id, fallback_fio=fio)}"
                f"Марка: {pass_request.car_brand}\n"
                f"Модель: {pass_request.car_model}\n"
                f"Номер: {pass_request.car_number}\n"
                f"Владелец: {(text_from_message(event) or '')}\n"
                f"Пункт назначения: {pass_request.destination}\n"
                f"Комментарий для СБ: {pass_request.security_comment or 'нет'}\n"
                f"Время создания: {pass_request.created_at.strftime('%d.%m.%Y %H:%M')}"
            )

            await answer_message(
                event,
                text,
                kb=get_edit_pass_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        await context.set_state(PermanentPassStates.EDITING_PASS)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, PermanentPassStates.AWAIT_EDIT_DESTINATION)
async def update_destination(event: MessageCreated, context: BaseContext):
    try:
        data = await context.get_data()
        pass_id = data.get('current_pass_id')

        async with AsyncSessionLocal() as session:
            pass_request = await session.get(PermanentPass, pass_id)
            pass_request.destination = (text_from_message(event) or '')
            await session.commit()

            result = await session.execute(
                select(
                    PermanentPass,
                    Resident.fio,
                    Resident.tg_id,
                    Resident.first_name,
                    Resident.last_name,
                )
                .join(Resident, Resident.id == PermanentPass.resident_id)
                .where(PermanentPass.id == pass_id)
            )
            pass_request, fio, res_tg_id, res_fn, res_ln = result.first()

            text = (
                f"ФИО: {fio_html(fio, res_tg_id)}\n"
                f"{profile_link_line_html(res_fn, res_ln, res_tg_id, fallback_fio=fio)}"
                f"Марка: {pass_request.car_brand}\n"
                f"Модель: {pass_request.car_model}\n"
                f"Номер: {pass_request.car_number}\n"
                f"Владелец: {pass_request.car_owner}\n"
                f"Пункт назначения: {(text_from_message(event) or '')}\n"
                f"Комментарий для СБ: {pass_request.security_comment}\n"
                f"Время создания: {pass_request.created_at.strftime('%d.%m.%Y %H:%M')}"
            )

            await answer_message(
                event,
                text,
                kb=get_edit_pass_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        await context.set_state(PermanentPassStates.EDITING_PASS)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


# Обновление комментария для СБ
@router.message_created(F.message.body.text, PermanentPassStates.AWAIT_EDIT_SECURITY_COMMENT)
async def update_security_comment(event: MessageCreated, context: BaseContext):
    try:
        data = await context.get_data()
        pass_id = data.get('current_pass_id')

        async with AsyncSessionLocal() as session:
            pass_request = await session.get(PermanentPass, pass_id)
            pass_request.security_comment = (text_from_message(event) or '')
            await session.commit()

            result = await session.execute(
                select(
                    PermanentPass,
                    Resident.fio,
                    Resident.tg_id,
                    Resident.first_name,
                    Resident.last_name,
                )
                .join(Resident, Resident.id == PermanentPass.resident_id)
                .where(PermanentPass.id == pass_id)
            )
            pass_request, fio, res_tg_id, res_fn, res_ln = result.first()

            text = (
                f"ФИО: {fio_html(fio, res_tg_id)}\n"
                f"{profile_link_line_html(res_fn, res_ln, res_tg_id, fallback_fio=fio)}"
                f"Марка: {pass_request.car_brand}\n"
                f"Модель: {pass_request.car_model}\n"
                f"Номер: {pass_request.car_number}\n"
                f"Владелец: {pass_request.car_owner}\n"
                f"Пункт назначения: {pass_request.destination}\n"
                f"Комментарий для СБ: {(text_from_message(event) or '')}\n"
                f"Время создания: {pass_request.created_at.strftime('%d.%m.%Y %H:%M')}"
            )

            await answer_message(
                event,
                text,
                kb=get_edit_pass_keyboard(),
                parse_mode=ParseMode.HTML,
            )
        await context.set_state(PermanentPassStates.EDITING_PASS)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "approved_permanent_passes")
async def show_approved_passes(event: MessageCallback, context: BaseContext):
    try:
        data = await context.get_data()
        current_page = data.get('pass_current_page', 0)

        async with AsyncSessionLocal() as session:
            # Получаем общее количество заявок
            total_count = await session.scalar(
                select(func.count(PermanentPass.id))
                .where(PermanentPass.status == 'approved')
            )

            # Получаем заявки для текущей страницы
            result = await session.execute(
                select(PermanentPass)
                .where(PermanentPass.status == 'approved')
                .order_by(PermanentPass.created_at.desc())
                .offset(current_page * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
            requests = result.scalars().all()

        if not requests:
            await edit_or_send_callback(
                bot, event, "Нет подтвержденных пропусков", None
            )
            return

        # Формируем кнопки
        buttons = []
        for req in requests:
            if req.resident_id:
                async with AsyncSessionLocal() as session:
                    result = await session.execute(select(Resident).where(Resident.id == req.resident_id))
                    resident = result.scalar()
                    fio_short = ' '.join(resident.fio.split()[:2])
                    text = f"{fio_short}_{req.car_number}"
            else:
                fio = req.security_comment.replace('Выписал', '')
                if 'Администратор' in fio:
                    fio_short = 'Администратор'
                else:
                    fio_short = ' '.join(fio.split()[:2])
                text = f"{fio_short}_{req.car_number}"
            buttons.append(
                [CallbackButton(text=text, payload=f"view_ap_pass_{req.id}")]
            )

        # Добавляем кнопки пагинации
        pagination_buttons = []
        if current_page > 0:
            pagination_buttons.append(
                CallbackButton(text="⬅️ Предыдущие", payload=f"ap_pass_prev_{current_page - 1}")
            )

        if (current_page + 1) * PAGE_SIZE < total_count:
            pagination_buttons.append(
                CallbackButton(text="Следующие ➡️", payload=f"ap_pass_next_{current_page + 1}")
            )

        if pagination_buttons:
            buttons.append(pagination_buttons)

        buttons.append(
            [CallbackButton(text="⬅️ Назад", payload="permanent_passes_menu")]
        )

        list_text = "Подтвержденные постоянные пропуска:"
        await edit_or_send_callback(bot, event, list_text, inline_kb(buttons))

        await context.update_data(
            pass_current_page=current_page,
            pass_total_count=total_count
        )
    except Exception as e:
        await bot.send_message(
            user_id=RAZRAB,
            text=f'{event.callback.user.user_id} - {str(e)}',
        )
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload.startswith("ap_pass_prev_") | F.callback.payload.startswith("ap_pass_next_"))
async def handle_approved_pass_pagination(event: MessageCallback, context: BaseContext):
    try:
        page = int(event.callback.payload.rsplit("_", 1)[-1])
        await context.update_data(pass_current_page=page)
        await show_approved_passes(event, context)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload.startswith("view_ap_pass_"))
async def view_pass_details(event: MessageCallback, context: BaseContext):
    try:
        pass_id = int(event.callback.payload.split("_")[-1])
        await context.update_data(current_pass_id=pass_id)

        async with AsyncSessionLocal() as session:
            # Получаем пропуск и связанного резидента
            result = await session.execute(
                select(PermanentPass)
                .where(PermanentPass.id == pass_id)
            )
            pass_request = result.scalar()

        if not pass_request:
            await callback_ack(bot, event, "Пропуск не найден")
            return

        profile_extra = ""
        if pass_request.resident_id:
            async with AsyncSessionLocal() as session:
                # Получаем пропуск и связанного резидента
                result = await session.execute(
                    select(Resident)
                    .where(Resident.id == pass_request.resident_id)
                )
                resident = result.scalar()
                fio_disp = fio_html(resident.fio, resident.tg_id)
                profile_extra = profile_link_line_html(
                    resident.first_name,
                    resident.last_name,
                    resident.tg_id,
                    fallback_fio=resident.fio,
                )
        else:
            raw = (
                pass_request.security_comment.replace("Выписал ", "")
                if pass_request.security_comment
                else ""
            )
            fio_disp = html_lib.escape(raw)

        # Формируем текст сообщения
        text = (
            f"ФИО: {fio_disp}\n"
            f"{profile_extra}"
            f"Марка: {html_lib.escape(str(pass_request.car_brand or ''))}\n"
            f"Модель: {html_lib.escape(str(pass_request.car_model or ''))}\n"
            f"Номер: {html_lib.escape(str(pass_request.car_number or ''))}\n"
            f"Владелец: {html_lib.escape(str(pass_request.car_owner or ''))}\n"
            f"Пункт назначения: {html_lib.escape(str(pass_request.destination or ''))}\n"
            f"Комментарий для СБ: {html_lib.escape(str(pass_request.security_comment or 'нет'))}\n"
            f"Время создания: {pass_request.created_at.strftime('%d.%m.%Y %H:%M')}\n"
            f"Время подтверждения: {pass_request.time_registration.strftime('%d.%m.%Y %H:%M')}"
        )

        # Формируем клавиатуру действий (добавляем кнопку Редактировать)
        keyboard = inline_kb([
            [CallbackButton(text="⬅️ Назад", payload="back_to_approved_passes")]
        ])

        await edit_or_send_callback(bot, event, text, keyboard, parse_mode=ParseMode.HTML)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "back_to_approved_passes")
async def back_to_approved_list(event: MessageCallback, context: BaseContext):
    try:
        await show_approved_passes(event, context)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "rejected_permanent_passes")
async def show_rejected_passes(event: MessageCallback, context: BaseContext):
    try:
        data = await context.get_data()
        current_page = data.get('pass_current_page', 0)

        async with AsyncSessionLocal() as session:
            # Получаем общее количество заявок
            total_count = await session.scalar(
                select(func.count(PermanentPass.id))
                .where(PermanentPass.status == 'rejected')
            )

            # Получаем заявки для текущей страницы
            result = await session.execute(
                select(
                    PermanentPass,
                    Resident.fio,
                    Resident.tg_id,
                    Resident.first_name,
                    Resident.last_name,
                )
                .join(Resident, Resident.id == PermanentPass.resident_id)
                .where(PermanentPass.status == 'rejected')
                .order_by(PermanentPass.created_at.desc())
                .offset(current_page * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
            requests = result.all()

        if not requests:
            await edit_or_send_callback(
                bot, event, "Нет отклоненных пропусков", None
            )
            return

        # Формируем кнопки
        buttons = []
        for req, fio, res_tg_id, res_fn, res_ln in requests:
            # Берем первые два слова из ФИО
            fio_short = ' '.join(fio.split()[:2])
            text = f"{req.id}_{fio_short}"
            buttons.append(
                [CallbackButton(text=text, payload=f"view_rej_pass_{req.id}")]
            )

        # Добавляем кнопки пагинации
        pagination_buttons = []
        if current_page > 0:
            pagination_buttons.append(
                CallbackButton(text="⬅️ Предыдущие", payload=f"rej_pass_prev_{current_page - 1}")
            )

        if (current_page + 1) * PAGE_SIZE < total_count:
            pagination_buttons.append(
                CallbackButton(text="Следующие ➡️", payload=f"rej_pass_next_{current_page + 1}")
            )

        if pagination_buttons:
            buttons.append(pagination_buttons)

        buttons.append(
            [CallbackButton(text="⬅️ Назад", payload="permanent_passes_menu")]
        )

        list_text = "Отклоненные постоянные пропуска:"
        await edit_or_send_callback(bot, event, list_text, inline_kb(buttons))

        await context.update_data(
            pass_current_page=current_page,
            pass_total_count=total_count
        )
    except Exception as e:
        await bot.send_message(
            user_id=RAZRAB,
            text=f'{event.callback.user.user_id} - {str(e)}',
        )
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload.startswith("rej_pass_prev_") | F.callback.payload.startswith("rej_pass_next_"))
async def handle_rejected_pass_pagination(event: MessageCallback, context: BaseContext):
    try:
        page = int(event.callback.payload.rsplit("_", 1)[-1])
        await context.update_data(pass_current_page=page)
        await show_rejected_passes(event, context)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload.startswith("view_rej_pass_"))
async def view_pass_details(event: MessageCallback, context: BaseContext):
    try:
        pass_id = int(event.callback.payload.split("_")[-1])
        await context.update_data(current_pass_id=pass_id)

        async with AsyncSessionLocal() as session:
            # Получаем пропуск и связанного резидента
            result = await session.execute(
                select(
                    PermanentPass,
                    Resident.fio,
                    Resident.tg_id,
                    Resident.first_name,
                    Resident.last_name,
                )
                .join(Resident, Resident.id == PermanentPass.resident_id)
                .where(PermanentPass.id == pass_id)
            )
            pass_request, fio, res_tg_id, res_fn, res_ln = result.first()

            if not pass_request:
                await callback_ack(bot, event, "Пропуск не найден")
                return

            # Формируем текст сообщения
            text = (
                f"ФИО: {fio_html(fio, res_tg_id)}\n"
                f"{profile_link_line_html(res_fn, res_ln, res_tg_id, fallback_fio=fio)}"
                f"Марка: {pass_request.car_brand}\n"
                f"Модель: {pass_request.car_model}\n"
                f"Номер: {pass_request.car_number}\n"
                f"Владелец: {pass_request.car_owner}\n"
                f"Пункт назначения: {pass_request.destination}\n"
                f"Комментарий для резидента: {pass_request.resident_comment or 'нет'}\n"
                f"Время создания: {pass_request.created_at.strftime('%d.%m.%Y %H:%M')}\n"
                f"Время отклонения: {pass_request.time_registration.strftime('%d.%m.%Y %H:%M')}"
            )

            # Формируем клавиатуру действий (добавляем кнопку Редактировать)
            keyboard = inline_kb([
                [CallbackButton(text="⬅️ Назад", payload="back_to_rejected_passes")]
            ])

            await edit_or_send_callback(bot, event, text, keyboard, parse_mode=ParseMode.HTML)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "back_to_rejected_passes")
async def back_to_rejected_list(event: MessageCallback, context: BaseContext):
    try:
        await show_rejected_passes(event, context)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)
