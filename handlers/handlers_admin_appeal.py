import asyncio
import datetime

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
from db.models import Appeal, AsyncSessionLocal, Resident, User
from filters import IsAdminOrManager
from max_helpers import (
    answer_message,
    callback_ack,
    edit_or_send_callback,
    fio_html,
    profile_link_line_html,
    send_user,
    states_in_group,
    text_from_message,
    user_id_from_message,
)

router = Router(router_id="admin_appeal")
router.filter(IsAdminOrManager())


class AnswerAppealStates(StatesGroup):
    INPUT_RESPONSE_TEXT = State()


class AppealViewStates(StatesGroup):
    VIEWING_ACTIVE = State()
    VIEWING_CLOSED = State()


def _appeals_management_kb() -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="Активные обращения", payload="active_appeals"))
    b.row(CallbackButton(text="Обращения закрытые", payload="closed_appeals"))
    b.row(CallbackButton(text="⬅️ Назад", payload="back_to_main"))
    return b


@router.message_callback(F.callback.payload == "appeals_management")
async def appeals_management(event: MessageCallback) -> None:
    try:
        await edit_or_send_callback(
            bot,
            event,
            "Управление обращениями в УК",
            _appeals_management_kb(),
        )
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "active_appeals")
async def show_active_appeals(event: MessageCallback, context: BaseContext) -> None:
    try:
        await context.set_state(AppealViewStates.VIEWING_ACTIVE)
        await context.update_data(appeal_page=0, appeal_status=False)
        await show_appeals(event, context)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "closed_appeals")
async def show_closed_appeals_admin(event: MessageCallback, context: BaseContext) -> None:
    try:
        await context.set_state(AppealViewStates.VIEWING_CLOSED)
        await context.update_data(appeal_page=0, appeal_status=True)
        await show_appeals(event, context)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


def _uid_from_appeals_event(event: MessageCallback | MessageCreated) -> int:
    if isinstance(event, MessageCallback):
        return event.callback.user.user_id
    uid = user_id_from_message(event)
    return uid if uid is not None else 0


async def show_appeals(event: MessageCallback | MessageCreated, context: BaseContext) -> None:
    try:
        data = await context.get_data()
        page = data.get("appeal_page", 0)
        status = data.get("appeal_status", False)

        async with AsyncSessionLocal() as session:
            total_count = await session.scalar(
                select(func.count(Appeal.id)).where(Appeal.status == status)
            )

            result = await session.execute(
                select(Appeal)
                .where(Appeal.status == status)
                .order_by(Appeal.created_at.desc())
                .offset(page * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
            appeals = result.scalars().all()

        if not appeals:
            text = "Нет обращений в этом разделе"
            if isinstance(event, MessageCallback):
                await callback_ack(bot, event, text)
            else:
                await answer_message(event, text)
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
        kb.row(CallbackButton(text="⬅️ Назад", payload="appeals_management"))

        status_text = "активные" if not status else "закрытые"
        text = f"Обращения ({status_text}):"

        if isinstance(event, MessageCallback):
            await edit_or_send_callback(bot, event, text, kb, parse_mode=ParseMode.HTML)
        else:
            await answer_message(event, text, kb)

    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{_uid_from_appeals_event(event)} - {e!s}")
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
async def view_appeal_details(event: MessageCallback, context: BaseContext) -> None:
    try:
        payload = event.callback.payload or ""
        appeal_id = int(payload.split("_")[-1])
        await context.update_data(current_appeal_id=appeal_id)

        async with AsyncSessionLocal() as session:
            appeal = await session.get(Appeal, appeal_id)
            if not appeal:
                await callback_ack(bot, event, "Обращение не найдено")
                return
            resident = await session.get(Resident, appeal.resident_id)

            if not appeal.status:
                text = (
                    f"<b>Обращение #{appeal.id}</b>\n\n"
                    f"<b>ФИО резидента:</b>\n{fio_html(resident.fio, resident.tg_id)}\n"
                    f"{profile_link_line_html(resident.first_name, resident.last_name, resident.tg_id, fallback_fio=resident.fio)}"
                    f"\n<b>Текст обращения:</b>\n{appeal.request_text}\n\n"
                    f"<b>Дата обращения:</b>\n{appeal.created_at.strftime('%d.%m.%Y')}"
                )
                kb = InlineKeyboardBuilder()
                kb.row(CallbackButton(text="✏️ Ответить на обращение", payload="answer_appeal"))
                kb.row(CallbackButton(text="⬅️ Назад", payload="back_to_appeals_list"))
                await edit_or_send_callback(bot, event, text, kb, parse_mode=ParseMode.HTML)
            else:
                text = (
                    f"<b>Обращение #{appeal.id}</b>\n\n"
                    f"<b>ФИО резидента:</b>\n{fio_html(resident.fio, resident.tg_id)}\n"
                    f"{profile_link_line_html(resident.first_name, resident.last_name, resident.tg_id, fallback_fio=resident.fio)}"
                    f"\n<b>Текст обращения:</b>\n{appeal.request_text}\n\n"
                    f"<b>Дата обращения:</b>\n{appeal.created_at.strftime('%d.%m.%Y')}\n\n"
                    f"<b>Ответ от УК:</b>\n{appeal.response_text}\n\n"
                    f"<b>Дата ответа:</b>\n{appeal.responsed_at.strftime('%d.%m.%Y')}"
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


@router.message_callback(F.callback.payload == "answer_appeal")
async def start_answer_appeal(event: MessageCallback, context: BaseContext) -> None:
    try:
        await context.set_state(AnswerAppealStates.INPUT_RESPONSE_TEXT)
        await send_user(bot, event.callback.user.user_id, "Введите текст ответа:")
        await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, AnswerAppealStates.INPUT_RESPONSE_TEXT)
async def save_appeal_response(event: MessageCreated, context: BaseContext) -> None:
    try:
        data = await context.get_data()
        appeal_id = data["current_appeal_id"]
        response_text = text_from_message(event)
        if response_text is None:
            return

        sender = event.message.sender
        if not sender:
            return

        resident_tg_id: int | None = None
        appeal_created_fmt: str = ""

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User).where(User.id == sender.user_id))
            responser = result.scalar()

            if not responser:
                responser = User(
                    id=sender.user_id,
                    username=sender.username,
                    first_name=sender.first_name,
                    last_name=sender.last_name,
                    time_start=datetime.datetime.now(),
                )
                session.add(responser)
                await session.commit()

            appeal = await session.get(Appeal, appeal_id)
            if not appeal:
                await answer_message(event, "❌ Обращение не найдено.")
                await context.clear()
                return

            appeal.response_text = response_text
            appeal.responser_id = responser.id
            appeal.responsed_at = datetime.datetime.now()
            appeal.status = True

            await session.commit()

            resident = await session.get(Resident, appeal.resident_id)
            if resident:
                resident_tg_id = resident.tg_id
            appeal_created_fmt = appeal.created_at.strftime("%d.%m.%Y")

        if resident_tg_id is not None:
            await bot.send_message(
                user_id=resident_tg_id,
                text=(
                    f"✅ По вашему обращению #{appeal_id} - {appeal_created_fmt} получен ответ от УК "
                    "(Обращения в УК > Обращения закрытые)"
                ),
            )

        await answer_message(event, "✅ Ответ успешно сохранен и отправлен резиденту!")
        await context.clear()
        await answer_message(event, "Управление обращениями в УК", _appeals_management_kb())

    except Exception as e:
        uid = event.message.sender.user_id if event.message.sender else 0
        await bot.send_message(user_id=RAZRAB, text=f"{uid} - {e!s}")
        await asyncio.sleep(0.05)
