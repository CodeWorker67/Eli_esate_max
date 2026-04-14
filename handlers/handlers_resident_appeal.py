import asyncio

from maxapi import F, Router
from maxapi.context.base import BaseContext
from maxapi.context.state_machine import State, StatesGroup
from maxapi.enums.parse_mode import ParseMode
from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from sqlalchemy import func, select

from bot import bot
from config import PAGE_SIZE, RAZRAB
from db.models import Appeal, AsyncSessionLocal, Resident
from db.util import get_active_admins_and_managers_tg_ids
from filters import IsResident
from max_helpers import (
    answer_message,
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

router = Router(router_id="resident_appeal")
router.filter(IsResident())


class AppealStates(StatesGroup):
    INPUT_REQUEST_TEXT = State()


class AppealViewStates(StatesGroup):
    VIEWING_PENDING = State()
    VIEWING_CLOSED = State()


def _appeals_menu_kb() -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="Подать обращение", payload="create_appeal"))
    b.row(CallbackButton(text="Обращения в ожидании", payload="pending_appeals"))
    b.row(CallbackButton(text="Обращения закрытые", payload="closed_appeals"))
    b.row(CallbackButton(text="Назад", payload="back_to_main_menu"))
    return b


@router.message_callback(F.callback.payload == "appeals_menu")
async def appeals_menu(event: MessageCallback) -> None:
    try:
        await callback_ack(bot, event)
        await send_user(
            bot,
            event.callback.user.user_id,
            "Управление обращениями в УК",
            _appeals_menu_kb(),
        )
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "create_appeal")
async def start_appeal_creation(event: MessageCallback, context: BaseContext) -> None:
    try:
        await context.set_state(AppealStates.INPUT_REQUEST_TEXT)
        await send_user(bot, event.callback.user.user_id, "Введите текст вашего обращения:")
        await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, AppealStates.INPUT_REQUEST_TEXT)
async def save_appeal(event: MessageCreated, context: BaseContext) -> None:
    try:
        text = text_from_message(event)
        if text is None:
            return

        sender = event.message.sender
        if not sender:
            await answer_message(event, "❌ Ошибка: не удалось определить пользователя")
            await context.clear()
            return

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Resident).where(Resident.tg_id == sender.user_id))
            resident = result.scalar()

            if not resident:
                await answer_message(event, "❌ Ошибка: резидент не найден")
                await context.clear()
                return

            new_appeal = Appeal(
                request_text=text,
                resident_id=resident.id,
                status=False,
            )
            session.add(new_appeal)
            await session.commit()
            resident_fio = resident.fio
            resident_max_id = resident.tg_id

        await answer_message(event, "✅ Ваше обращение успешно отправлено в УК!")
        tg_ids = await get_active_admins_and_managers_tg_ids()
        for tg_id in tg_ids:
            try:
                await send_user(
                    bot,
                    tg_id,
                    text=(
                        "Поступило обращение от резидента "
                        f"{fio_html(resident_fio, resident_max_id)}.\n"
                        "(Обращения к УК > Обращения в ожидании)"
                    ),
                    kb=main_menu_inline_button_kb(),
                    parse_mode=ParseMode.HTML,
                )
                await asyncio.sleep(0.05)
            except Exception:
                pass
        await context.clear()
        await answer_message(event, "Управление обращениями в УК", _appeals_menu_kb())

    except Exception as e:
        uid = event.message.sender.user_id if event.message.sender else 0
        await bot.send_message(user_id=RAZRAB, text=f"{uid} - {e!s}")
        await asyncio.sleep(0.05)


def _uid_from_event(event: MessageCallback | MessageCreated) -> int:
    if isinstance(event, MessageCallback):
        return event.callback.user.user_id
    uid = user_id_from_message(event)
    return uid if uid is not None else 0


@router.message_callback(F.callback.payload == "pending_appeals")
async def show_pending_appeals(event: MessageCallback, context: BaseContext) -> None:
    try:
        await context.set_state(AppealViewStates.VIEWING_PENDING)
        await context.update_data(appeal_page=0, appeal_status=False)
        await show_appeals(event, context)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "closed_appeals")
async def show_closed_appeals_resident(event: MessageCallback, context: BaseContext) -> None:
    try:
        await context.set_state(AppealViewStates.VIEWING_CLOSED)
        await context.update_data(appeal_page=0, appeal_status=True)
        await show_appeals(event, context)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


async def show_appeals(event: MessageCallback | MessageCreated, context: BaseContext) -> None:
    try:
        data = await context.get_data()
        page = data.get("appeal_page", 0)
        status = data.get("appeal_status", False)

        uid = _uid_from_event(event)

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Resident).where(Resident.tg_id == uid))
            resident = result.scalar()

            if not resident:
                if isinstance(event, MessageCallback):
                    await callback_ack(bot, event, "❌ Резидент не найден")
                else:
                    await answer_message(event, "❌ Резидент не найден")
                return

            total_count = await session.scalar(
                select(func.count(Appeal.id)).where(
                    Appeal.resident_id == resident.id,
                    Appeal.status == status,
                )
            )

            result = await session.execute(
                select(Appeal)
                .where(
                    Appeal.resident_id == resident.id,
                    Appeal.status == status,
                )
                .order_by(Appeal.created_at.desc())
                .offset(page * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
            appeals = result.scalars().all()

        if not appeals:
            msg = "У вас нет обращений в этом разделе"
            if isinstance(event, MessageCallback):
                await callback_ack(bot, event, msg)
            else:
                await answer_message(event, msg)
            return

        pagination_row: list[CallbackButton] = []
        if page > 0:
            pagination_row.append(CallbackButton(text="⬅️ Предыдущие", payload="appeal_prev"))
        if total_count is not None and (page + 1) * PAGE_SIZE < total_count:
            pagination_row.append(CallbackButton(text="Следующие ➡️", payload="appeal_next"))

        kb = InlineKeyboardBuilder()
        for appeal in appeals:
            btn_text = f"Обращение #{appeal.id} - {appeal.created_at.strftime('%d.%m.%Y')}"
            kb.row(CallbackButton(text=btn_text, payload=f"view_appeal_{appeal.id}"))
        if pagination_row:
            kb.row(*pagination_row)
        kb.row(CallbackButton(text="⬅️ Назад", payload="appeals_menu"))

        status_text = "в ожидании" if not status else "закрытые"
        list_text = f"Ваши обращения ({status_text}):"

        if isinstance(event, MessageCallback):
            await edit_or_send_callback(bot, event, list_text, kb, parse_mode=ParseMode.HTML)
        else:
            await answer_message(event, list_text, kb)

    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{_uid_from_event(event)} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(
    F.callback.payload == "appeal_prev",
    *states_in_group(AppealViewStates),
)
async def handle_appeal_prev(event: MessageCallback, context: BaseContext) -> None:
    try:
        data = await context.get_data()
        current_page = data.get("appeal_page", 0)
        if current_page > 0:
            await context.update_data(appeal_page=current_page - 1)
            await show_appeals(event, context)
        else:
            await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(
    F.callback.payload == "appeal_next",
    *states_in_group(AppealViewStates),
)
async def handle_appeal_next(event: MessageCallback, context: BaseContext) -> None:
    try:
        data = await context.get_data()
        current_page = data.get("appeal_page", 0)
        await context.update_data(appeal_page=current_page + 1)
        await show_appeals(event, context)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload.startswith("view_appeal_"))
async def view_appeal_details(event: MessageCallback) -> None:
    try:
        payload = event.callback.payload or ""
        appeal_id = int(payload.split("_")[-1])

        async with AsyncSessionLocal() as session:
            appeal = await session.get(Appeal, appeal_id)
            if not appeal:
                await callback_ack(bot, event, "Обращение не найдено")
                return

            resident = await session.get(Resident, appeal.resident_id)
            fio_line = (
                f"<b>ФИО резидента:</b>\n{fio_html(resident.fio, resident.tg_id)}\n"
                f"{profile_link_line_html(resident.first_name, resident.last_name, resident.tg_id, fallback_fio=resident.fio)}"
                if resident
                else "<b>ФИО резидента:</b>\n<i>не найдено</i>"
            )

            if appeal.status:
                text = (
                    f"<b>Обращение #{appeal.id}</b>\n\n"
                    f"{fio_line}\n\n"
                    f"<b>Текст обращения:</b>\n{appeal.request_text}\n\n"
                    f"<b>Дата обращения:</b>\n{appeal.created_at.strftime('%d.%m.%Y')}\n\n"
                    f"<b>Ответ от УК:</b>\n{appeal.response_text}\n\n"
                    f"<b>Дата ответа:</b>\n{appeal.responsed_at.strftime('%d.%m.%Y')}"
                )
            else:
                text = (
                    f"<b>Обращение #{appeal.id}</b>\n\n"
                    f"{fio_line}\n\n"
                    f"<b>Текст обращения:</b>\n{appeal.request_text}\n\n"
                    f"<b>Дата обращения:</b>\n{appeal.created_at.strftime('%d.%m.%Y')}\n\n"
                    f"<i>Статус: в обработке УК</i>"
                )

            kb = InlineKeyboardBuilder()
            kb.row(CallbackButton(text="⬅️ Назад", payload="back_to_appeals_list"))
            await edit_or_send_callback(bot, event, text, kb, parse_mode=ParseMode.HTML)

    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "back_to_appeals_list")
async def back_to_appeals_list(event: MessageCallback, context: BaseContext) -> None:
    try:
        await show_appeals(event, context)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)
