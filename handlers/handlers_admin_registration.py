import asyncio
import datetime
import html as html_lib

from maxapi import F, Router
from maxapi.context import MemoryContext
from maxapi.context.state_machine import State, StatesGroup
from maxapi.enums.parse_mode import ParseMode
from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from bot import bot
from config import RAZRAB
from db.models import (
    AsyncSessionLocal,
    Contractor,
    ContractorContractorRequest,
    ContractorRegistrationRequest,
    RegistrationRequest,
    Resident,
    ResidentContractorRequest,
)
from filters import IsAdminOrManager
from keyboards import contractor_main_menu_kb, resident_main_kb
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

router = Router(router_id="admin_registration")
router.filter(IsAdminOrManager())


class RegistrationRequestStates(StatesGroup):
    AWAIT_REJECT_RESIDENT_COMMENT = State()
    AWAIT_REJECT_SUBCONTRACTOR_COMMENT = State()
    AWAIT_EDIT_COMPANY = State()
    AWAIT_EDIT_POSITION = State()
    AWAIT_EDIT_CONTRACTOR_FIO = State()
    EDITING_CONTRACTOR_REQUEST = State()
    VIEWING_CONTRACTOR_REQUEST = State()
    AWAIT_REJECT_CONTRACTOR_COMMENT = State()
    VIEWING_REQUEST = State()
    EDITING_REQUEST = State()
    AWAIT_EDIT_FIO = State()
    AWAIT_EDIT_PLOT = State()
    AWAIT_REJECT_COMMENT = State()


def edit_keyboard_contractor() -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.row(
        CallbackButton(text="ФИО", payload="edit_contractorfio"),
        CallbackButton(text="Компания", payload="edit_contractorcompany"),
    )
    b.row(CallbackButton(text="Должность", payload="edit_contractorposition"))
    b.row(CallbackButton(text="✅ Готово", payload="edit_finishcontractor"))
    return b


def get_registration_menu() -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="Регистрация резидентов", payload="registration_requests"))
    b.row(CallbackButton(text="Регистрация подрядчиков", payload="contractor_requests"))
    b.row(
        CallbackButton(
            text="Заявки подрядчиков от резидентов",
            payload="resident_contractor_requests",
        )
    )
    b.row(
        CallbackButton(
            text="Заявки субподрядчиков от подрядчиков",
            payload="contractor_contractor_requests",
        )
    )
    b.row(CallbackButton(text="⬅️ Назад", payload="back_to_main"))
    return b


def edit_keyboard_resident() -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.row(
        CallbackButton(text="ФИО", payload="edit_fio"),
        CallbackButton(text="Номер участка", payload="edit_plot"),
    )
    b.row(CallbackButton(text="✅ Готово", payload="edit_finish"))
    return b


def restart_kb() -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="Заполнить заново", payload="restart"))
    return b


@router.message_callback(F.callback.payload == "registration_menu")
async def show_registration_menu(event: MessageCallback) -> None:
    try:
        await edit_or_send_callback(
            bot, event, "Меню регистрации:", get_registration_menu(), parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "registration_requests")
async def show_pending_requests(event: MessageCallback) -> None:
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(RegistrationRequest).filter(RegistrationRequest.status == "pending")
            )
            requests = result.scalars().all()

            if not requests:
                await callback_ack(bot, event, "Нет заявок в ожидании")
                return

            b = InlineKeyboardBuilder()
            for req in requests:
                b.row(CallbackButton(text=f"{req.fio}", payload=f"view_request_{req.id}"))
            b.row(CallbackButton(text="⬅️ Назад", payload="registration_menu"))

            await edit_or_send_callback(
                bot, event, "Заявки на регистрацию:", b, parse_mode=ParseMode.HTML
            )
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "contractor_requests")
async def show_contractor_requests(event: MessageCallback) -> None:
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ContractorRegistrationRequest).filter(
                    ContractorRegistrationRequest.status == "pending"
                )
            )
            requests = result.scalars().all()

            if not requests:
                await callback_ack(bot, event, "Нет заявок подрядчиков")
                return

            b = InlineKeyboardBuilder()
            for req in requests:
                b.row(
                    CallbackButton(
                        text=f"{req.company}_{req.position}",
                        payload=f"view_cont_request_{req.id}",
                    )
                )
            b.row(CallbackButton(text="⬅️ Назад", payload="registration_menu"))

            await edit_or_send_callback(bot, event, "Заявки подрядчиков:", b, parse_mode=ParseMode.HTML)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload.startswith("view_request_"))
async def view_request_details(event: MessageCallback, context: MemoryContext) -> None:
    try:
        request_id = int(event.callback.payload.split("_")[-1])

        async with AsyncSessionLocal() as session:
            request = await session.get(RegistrationRequest, request_id)

            await context.update_data(current_request_id=request_id)

            text = (
                f"ФИО: {fio_html(request.fio, request.tg_id)}\n"
                f"{profile_link_line_html(request.first_name, request.last_name, request.tg_id, fallback_fio=request.fio)}"
                f"Участок: {html_lib.escape(str(request.plot_number or ''))}"
            )

            b = InlineKeyboardBuilder()
            b.row(CallbackButton(text="✅ Одобрить", payload="approve_request"))
            b.row(CallbackButton(text="✏️ Редактировать", payload="edit_request"))
            b.row(CallbackButton(text="❌ Отклонить", payload="reject_request"))
            b.row(CallbackButton(text="⬅️ Назад", payload="registration_requests"))

            await edit_or_send_callback(bot, event, text, b, parse_mode=ParseMode.HTML)
        await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload.startswith("view_cont_request_"))
async def view_contractor_request(event: MessageCallback, context: MemoryContext) -> None:
    try:
        request_id = int(event.callback.payload.split("_")[-1])
        await context.update_data(current_contractor_request_id=request_id)

        async with AsyncSessionLocal() as session:
            request = await session.get(ContractorRegistrationRequest, request_id)
            text = (
                f"ФИО: {fio_html(request.fio, request.tg_id)}\n"
                f"{profile_link_line_html(request.first_name, request.last_name, request.tg_id, fallback_fio=request.fio)}"
                f"Компания: {html_lib.escape(str(request.company or ''))}\n"
                f"Должность: {html_lib.escape(str(request.position or ''))}\n"
                f"Принадлежность: {html_lib.escape(str(request.affiliation or ''))}\n"
            )

            b = InlineKeyboardBuilder()
            b.row(CallbackButton(text="✅ Одобрить", payload="approve_contractor_request"))
            b.row(CallbackButton(text="✏️ Редактировать", payload="edit_contractor_request"))
            b.row(CallbackButton(text="❌ Отклонить", payload="reject_contractor_request"))
            b.row(CallbackButton(text="⬅️ Назад", payload="contractor_requests"))

            await edit_or_send_callback(bot, event, text, b, parse_mode=ParseMode.HTML)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "approve_request")
async def approve_request(event: MessageCallback, context: MemoryContext) -> None:
    try:
        data = await context.get_data()
        request_id = data["current_request_id"]

        async with AsyncSessionLocal() as session:
            request = await session.get(RegistrationRequest, request_id)
            resident = await session.get(Resident, request.resident_id)

            resident.fio = request.fio
            resident.plot_number = request.plot_number
            resident.photo_id = request.photo_id
            resident.tg_id = request.tg_id
            resident.username = request.username
            resident.first_name = request.first_name
            resident.last_name = request.last_name
            resident.time_registration = datetime.datetime.now()
            resident.status = True

            request.status = "approved"
            await session.commit()

            await send_user(
                bot,
                request.tg_id,
                text=(
                    "🎉 Поздравляем с успешной регистрацией в качестве резидента!\n\n"
                    f"👤 ФИО: {fio_html(resident.fio, resident.tg_id)}\n"
                    f"{profile_link_line_html(resident.first_name, resident.last_name, resident.tg_id, fallback_fio=resident.fio)}"
                    f"🏠 Номер участка: {html_lib.escape(str(resident.plot_number or ''))}\n\n"
                    "Для управления используйте меню ниже или кнопку «Главное меню»."
                ),
                kb=resident_main_kb,
                parse_mode=ParseMode.HTML,
            )

            uid = event.callback.user.user_id
            await bot.send_message(user_id=uid, text="✅ Заявка одобрена")
            await send_user(
                bot,
                uid,
                "Меню регистрации:",
                get_registration_menu(),
                parse_mode=ParseMode.HTML,
            )
            await context.clear()
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "approve_contractor_request")
async def approve_contractor_request(event: MessageCallback, context: MemoryContext) -> None:
    try:
        data = await context.get_data()
        request_id = data["current_contractor_request_id"]

        async with AsyncSessionLocal() as session:
            request = await session.get(ContractorRegistrationRequest, request_id)
            contractor = await session.get(Contractor, request.contractor_id)

            contractor.fio = request.fio
            contractor.company = request.company
            contractor.position = request.position
            contractor.affiliation = request.affiliation
            contractor.tg_id = request.tg_id
            contractor.username = request.username
            contractor.first_name = request.first_name
            contractor.last_name = request.last_name
            contractor.status = True
            contractor.time_registration = datetime.datetime.now()

            request.status = "approved"
            await session.commit()

            await send_user(
                bot,
                request.tg_id,
                text=(
                    "🎉 Поздравляем с успешной регистрацией в качестве подрядчика!\n\n"
                    f"ФИО: {fio_html(contractor.fio, contractor.tg_id)}\n"
                    f"{profile_link_line_html(contractor.first_name, contractor.last_name, contractor.tg_id, fallback_fio=contractor.fio)}"
                    f"Компания: {html_lib.escape(str(contractor.company or ''))}\n"
                    f"Должность: {html_lib.escape(str(contractor.position or ''))}\n\n"
                    "Для управления используйте меню ниже или кнопку «Главное меню»."
                ),
                kb=contractor_main_menu_kb(bool(contractor.can_add_contractor)),
                parse_mode=ParseMode.HTML,
            )

        uid = event.callback.user.user_id
        if event.message and event.message.body:
            await bot.edit_message(
                message_id=event.message.body.mid,
                text="✅ Заявка одобрена",
                attachments=None,
                parse_mode=ParseMode.HTML,
            )
        else:
            await bot.send_message(user_id=uid, text="✅ Заявка одобрена")
        await send_user(
            bot,
            uid,
            "Меню регистрации:",
            get_registration_menu(),
            parse_mode=ParseMode.HTML,
        )
        await context.clear()
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "edit_request")
async def start_editing_resident(event: MessageCallback, context: MemoryContext) -> None:
    try:
        if not event.message or not event.message.body:
            raise ValueError("no body")
        text = event.message.body.text or " "
        await bot.edit_message(
            message_id=event.message.body.mid,
            text=text,
            attachments=[edit_keyboard_resident().as_markup()],
            parse_mode=ParseMode.HTML,
        )
        await context.set_state(RegistrationRequestStates.EDITING_REQUEST)
        await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "edit_contractor_request")
async def start_contractor_editing(event: MessageCallback, context: MemoryContext) -> None:
    try:
        if not event.message or not event.message.body:
            raise ValueError("no body")
        text = event.message.body.text or " "
        await bot.edit_message(
            message_id=event.message.body.mid,
            text=text,
            attachments=[edit_keyboard_contractor().as_markup()],
            parse_mode=ParseMode.HTML,
        )
        await context.set_state(RegistrationRequestStates.EDITING_CONTRACTOR_REQUEST)
        await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(
    F.callback.payload == "edit_finish",
    RegistrationRequestStates.EDITING_REQUEST,
)
async def finish_editing_resident(event: MessageCallback, context: MemoryContext) -> None:
    try:
        data = await context.get_data()
        request_id = data["current_request_id"]

        async with AsyncSessionLocal() as session:
            request = await session.get(RegistrationRequest, request_id)

            text = (
                f"ФИО: {fio_html(request.fio, request.tg_id)}\n"
                f"{profile_link_line_html(request.first_name, request.last_name, request.tg_id, fallback_fio=request.fio)}"
                f"Участок: {html_lib.escape(str(request.plot_number or ''))}"
            )

            b = InlineKeyboardBuilder()
            b.row(CallbackButton(text="✅ Одобрить", payload="approve_request"))
            b.row(CallbackButton(text="✏️ Редактировать", payload="edit_request"))
            b.row(CallbackButton(text="❌ Отклонить", payload="reject_request"))
            b.row(CallbackButton(text="⬅️ Назад", payload="requests"))

            await edit_or_send_callback(bot, event, text, b, parse_mode=ParseMode.HTML)

        await context.set_state(RegistrationRequestStates.VIEWING_REQUEST)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(
    F.callback.payload == "edit_finishcontractor",
    RegistrationRequestStates.EDITING_CONTRACTOR_REQUEST,
)
async def finish_editing_contractor(event: MessageCallback, context: MemoryContext) -> None:
    try:
        data = await context.get_data()
        request_id = data["current_contractor_request_id"]

        async with AsyncSessionLocal() as session:
            request = await session.get(ContractorRegistrationRequest, request_id)

            text = (
                f"ФИО: {fio_html(request.fio, request.tg_id)}\n"
                f"{profile_link_line_html(request.first_name, request.last_name, request.tg_id, fallback_fio=request.fio)}"
                f"Компания: {html_lib.escape(str(request.company or ''))}\n"
                f"Должность: {html_lib.escape(str(request.position or ''))}\n"
                f"Принадлежность: {html_lib.escape(str(request.affiliation or ''))}\n"
            )

            b = InlineKeyboardBuilder()
            b.row(CallbackButton(text="✅ Одобрить", payload="approve_contractor_request"))
            b.row(CallbackButton(text="✏️ Редактировать", payload="edit_contractor_request"))
            b.row(CallbackButton(text="❌ Отклонить", payload="reject_contractor_request"))
            b.row(CallbackButton(text="⬅️ Назад", payload="contractor_requests"))

            await edit_or_send_callback(bot, event, text, b, parse_mode=ParseMode.HTML)

        await context.set_state(RegistrationRequestStates.VIEWING_CONTRACTOR_REQUEST)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(
    F.callback.payload.startswith("edit_"),
    RegistrationRequestStates.EDITING_CONTRACTOR_REQUEST,
)
async def handle_edit_actions_contractor(event: MessageCallback, context: MemoryContext) -> None:
    try:
        action = event.callback.payload.split("_")[-1]
        uid = event.callback.user.user_id

        if action == "contractorfio":
            await bot.send_message(user_id=uid, text="Введите новое ФИО:")
            await context.set_state(RegistrationRequestStates.AWAIT_EDIT_CONTRACTOR_FIO)
        elif action == "contractorcompany":
            await bot.send_message(user_id=uid, text="Введите новое название компании:")
            await context.set_state(RegistrationRequestStates.AWAIT_EDIT_COMPANY)
        elif action == "contractorposition":
            await bot.send_message(user_id=uid, text="Введите новую должность:")
            await context.set_state(RegistrationRequestStates.AWAIT_EDIT_POSITION)
        await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(
    F.callback.payload.startswith("edit_"),
    RegistrationRequestStates.EDITING_REQUEST,
)
async def handle_edit_actions_resident(event: MessageCallback, context: MemoryContext) -> None:
    try:
        action = event.callback.payload.split("_")[-1]
        uid = event.callback.user.user_id

        if action == "fio":
            await bot.send_message(user_id=uid, text="Введите новое ФИО:")
            await context.set_state(RegistrationRequestStates.AWAIT_EDIT_FIO)
        elif action == "plot":
            await bot.send_message(user_id=uid, text="Введите новый номер участка:")
            await context.set_state(RegistrationRequestStates.AWAIT_EDIT_PLOT)
        await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_created(RegistrationRequestStates.AWAIT_EDIT_FIO)
async def update_fio_resident(event: MessageCreated, context: MemoryContext) -> None:
    try:
        msg = text_from_message(event)
        if not msg:
            return
        data = await context.get_data()
        request_id = data["current_request_id"]
        async with AsyncSessionLocal() as session:
            request = await session.get(RegistrationRequest, request_id)
            request.fio = msg
            await session.commit()
            text = (
                f"ФИО: {fio_html(msg, request.tg_id)}\n"
                f"{profile_link_line_html(request.first_name, request.last_name, request.tg_id, fallback_fio=msg)}"
                f"Участок: {html_lib.escape(str(request.plot_number or ''))}"
            )
            await answer_message(event, text, edit_keyboard_resident(), parse_mode=ParseMode.HTML)
        await context.set_state(RegistrationRequestStates.EDITING_REQUEST)
    except Exception as e:
        uid = event.message.sender.user_id if event.message.sender else RAZRAB
        await bot.send_message(user_id=RAZRAB, text=f"{uid} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_created(RegistrationRequestStates.AWAIT_EDIT_CONTRACTOR_FIO)
async def update_fio_contractor(event: MessageCreated, context: MemoryContext) -> None:
    try:
        msg = text_from_message(event)
        if not msg:
            return
        data = await context.get_data()
        request_id = data["current_contractor_request_id"]
        uid = user_id_from_message(event)
        if uid is None:
            return
        async with AsyncSessionLocal() as session:
            request = await session.get(ContractorRegistrationRequest, request_id)
            request.fio = msg
            await session.commit()
            text = (
                f"ФИО: {fio_html(msg, request.tg_id)}\n"
                f"{profile_link_line_html(request.first_name, request.last_name, request.tg_id, fallback_fio=msg)}"
                f"Компания: {html_lib.escape(str(request.company or ''))}\n"
                f"Должность: {html_lib.escape(str(request.position or ''))}\n"
                f"Принадлежность: {html_lib.escape(str(request.affiliation or ''))}\n"
            )
            await send_user(bot, uid, text, edit_keyboard_contractor(), parse_mode=ParseMode.HTML)
        await context.set_state(RegistrationRequestStates.EDITING_CONTRACTOR_REQUEST)
    except Exception as e:
        uid = event.message.sender.user_id if event.message.sender else RAZRAB
        await bot.send_message(user_id=RAZRAB, text=f"{uid} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_created(RegistrationRequestStates.AWAIT_EDIT_COMPANY)
async def update_company(event: MessageCreated, context: MemoryContext) -> None:
    try:
        msg = text_from_message(event)
        if not msg:
            return
        data = await context.get_data()
        request_id = data["current_contractor_request_id"]
        uid = user_id_from_message(event)
        if uid is None:
            return
        async with AsyncSessionLocal() as session:
            request = await session.get(ContractorRegistrationRequest, request_id)
            request.company = msg
            await session.commit()
            text = (
                f"ФИО: {fio_html(request.fio, request.tg_id)}\n"
                f"{profile_link_line_html(request.first_name, request.last_name, request.tg_id, fallback_fio=request.fio)}"
                f"Компания: {html_lib.escape(msg)}\n"
                f"Должность: {html_lib.escape(str(request.position or ''))}\n"
                f"Принадлежность: {html_lib.escape(str(request.affiliation or ''))}\n"
            )
            await send_user(bot, uid, text, edit_keyboard_contractor(), parse_mode=ParseMode.HTML)
        await context.set_state(RegistrationRequestStates.EDITING_CONTRACTOR_REQUEST)
    except Exception as e:
        uid = event.message.sender.user_id if event.message.sender else RAZRAB
        await bot.send_message(user_id=RAZRAB, text=f"{uid} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_created(RegistrationRequestStates.AWAIT_EDIT_POSITION)
async def update_position(event: MessageCreated, context: MemoryContext) -> None:
    try:
        msg = text_from_message(event)
        if not msg:
            return
        data = await context.get_data()
        request_id = data["current_contractor_request_id"]
        uid = user_id_from_message(event)
        if uid is None:
            return
        async with AsyncSessionLocal() as session:
            request = await session.get(ContractorRegistrationRequest, request_id)
            request.position = msg
            await session.commit()
            text = (
                f"ФИО: {fio_html(request.fio, request.tg_id)}\n"
                f"{profile_link_line_html(request.first_name, request.last_name, request.tg_id, fallback_fio=request.fio)}"
                f"Компания: {html_lib.escape(str(request.company or ''))}\n"
                f"Должность: {html_lib.escape(msg)}\n"
                f"Принадлежность: {html_lib.escape(str(request.affiliation or ''))}\n"
            )
            await send_user(bot, uid, text, edit_keyboard_contractor(), parse_mode=ParseMode.HTML)
        await context.set_state(RegistrationRequestStates.EDITING_CONTRACTOR_REQUEST)
    except Exception as e:
        uid = event.message.sender.user_id if event.message.sender else RAZRAB
        await bot.send_message(user_id=RAZRAB, text=f"{uid} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_created(RegistrationRequestStates.AWAIT_EDIT_PLOT)
async def update_plot_resident(event: MessageCreated, context: MemoryContext) -> None:
    try:
        msg = text_from_message(event)
        if not msg:
            return
        data = await context.get_data()
        request_id = data["current_request_id"]

        async with AsyncSessionLocal() as session:
            request = await session.get(RegistrationRequest, request_id)
            request.plot_number = msg
            await session.commit()
            text = (
                f"ФИО: {fio_html(request.fio, request.tg_id)}\n"
                f"{profile_link_line_html(request.first_name, request.last_name, request.tg_id, fallback_fio=request.fio)}"
                f"Участок: {html_lib.escape(msg)}"
            )
            await answer_message(event, text, edit_keyboard_resident(), parse_mode=ParseMode.HTML)
        await context.set_state(RegistrationRequestStates.EDITING_REQUEST)
    except Exception as e:
        uid = event.message.sender.user_id if event.message.sender else RAZRAB
        await bot.send_message(user_id=RAZRAB, text=f"{uid} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "reject_request")
async def start_reject_resident(event: MessageCallback, context: MemoryContext) -> None:
    try:
        if event.message:
            await event.message.answer(text="Введите комментарий для отклонения:")
        else:
            await bot.send_message(
                user_id=event.callback.user.user_id,
                text="Введите комментарий для отклонения:",
            )
        await context.set_state(RegistrationRequestStates.AWAIT_REJECT_COMMENT)
        await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_created(RegistrationRequestStates.AWAIT_REJECT_CONTRACTOR_COMMENT)
async def reject_contractor_msg(event: MessageCreated, context: MemoryContext) -> None:
    try:
        msg = text_from_message(event)
        if not msg:
            return
        data = await context.get_data()
        request_id = data["current_contractor_request_id"]
        uid = event.message.sender.user_id if event.message.sender else None

        async with AsyncSessionLocal() as session:
            request = await session.get(ContractorRegistrationRequest, request_id)
            request.status = "rejected"
            request.admin_comment = msg
            await session.commit()

            await send_user(
                bot,
                request.tg_id,
                f"❌ Ваша заявка отклонена.\nПричина: {msg}",
                restart_kb(),
                parse_mode=ParseMode.HTML,
            )

        await answer_message(event, "Заявка отклонена!")
        await answer_message(event, "Меню регистрации:", get_registration_menu(), parse_mode=ParseMode.HTML)
        await context.clear()
    except Exception as e:
        uid = event.message.sender.user_id if event.message.sender else RAZRAB
        await bot.send_message(user_id=RAZRAB, text=f"{uid} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "reject_contractor_request")
async def start_reject_contractor(event: MessageCallback, context: MemoryContext) -> None:
    try:
        if event.message:
            await event.message.answer(text="Введите комментарий для отклонения:")
        else:
            await bot.send_message(
                user_id=event.callback.user.user_id,
                text="Введите комментарий для отклонения:",
            )
        await context.set_state(RegistrationRequestStates.AWAIT_REJECT_CONTRACTOR_COMMENT)
        await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_created(RegistrationRequestStates.AWAIT_REJECT_COMMENT)
async def reject_resident_msg(event: MessageCreated, context: MemoryContext) -> None:
    try:
        msg = text_from_message(event)
        if not msg:
            return
        data = await context.get_data()
        request_id = data["current_request_id"]

        async with AsyncSessionLocal() as session:
            request = await session.get(RegistrationRequest, request_id)
            request.status = "rejected"
            request.admin_comment = msg
            await session.commit()

            await send_user(
                bot,
                request.tg_id,
                f"❌ Ваша заявка отклонена.\nПричина: {msg}",
                restart_kb(),
                parse_mode=ParseMode.HTML,
            )

        await answer_message(event, "Заявка отклонена!")
        await answer_message(event, "Меню регистрации:", get_registration_menu(), parse_mode=ParseMode.HTML)
        await context.clear()
    except Exception as e:
        uid = event.message.sender.user_id if event.message.sender else RAZRAB
        await bot.send_message(user_id=RAZRAB, text=f"{uid} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "resident_contractor_requests")
async def show_resident_contractor_requests(event: MessageCallback) -> None:
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ResidentContractorRequest).filter(ResidentContractorRequest.status == "pending")
            )
            requests = result.scalars().all()
            b = InlineKeyboardBuilder()
            for req in requests:
                resident = await session.get(Resident, req.resident_id)
                b.row(
                    CallbackButton(
                        text=f"{resident.fio}",
                        payload=f"view_rescont_request_{req.id}",
                    )
                )
            b.row(CallbackButton(text="⬅️ Назад", payload="registration_menu"))

            await edit_or_send_callback(
                bot,
                event,
                "Заявки на подрядчиков от резидентов:",
                b,
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload.startswith("view_rescont_request_"))
async def view_resident_request(event: MessageCallback, context: MemoryContext) -> None:
    try:
        request_id = int(event.callback.payload.split("_")[-1])
        await context.update_data(current_resident_request_id=request_id)

        async with AsyncSessionLocal() as session:
            request = await session.get(ResidentContractorRequest, request_id)
            resident = await session.get(Resident, request.resident_id)

            res_plink = profile_link_line_html(
                resident.first_name,
                resident.last_name,
                resident.tg_id,
                fallback_fio=resident.fio,
            ).rstrip("\n")
            text = (
                f"📱 Телефон: {html_lib.escape(str(request.phone or ''))}\n"
                f"🏗 Виды работ: {html_lib.escape(str(request.work_types or ''))}\n"
                f"👤 Резидент: {fio_html(resident.fio, resident.tg_id)} (ID: {resident.id})\n"
                f"{res_plink}"
            )

            b = InlineKeyboardBuilder()
            b.row(CallbackButton(text="✅ Одобрить", payload="approve_rescont_request"))
            b.row(CallbackButton(text="❌ Отклонить", payload="reject_rescont_request"))

            await edit_or_send_callback(bot, event, text, b, parse_mode=ParseMode.HTML)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "approve_rescont_request")
async def approve_resident_request(event: MessageCallback, context: MemoryContext) -> None:
    try:
        data = await context.get_data()
        request_id = data["current_resident_request_id"]

        async with AsyncSessionLocal() as session:
            request = await session.get(ResidentContractorRequest, request_id)
            resident = await session.get(Resident, request.resident_id)
            phone = ""
            phone_ = request.phone
            for p in phone_:
                if p.isdigit() or p == "+":
                    phone += p
            phone = phone.replace("+7", "8")
            if phone and phone[0] == "7":
                phone = "8" + phone[1:]
            new_contractor = Contractor(
                phone=phone,
                work_types=request.work_types,
                affiliation=f"{resident.id}_{resident.fio}",
                status=False,
            )
            session.add(new_contractor)
            await session.commit()

            request.status = "approved"
            await session.commit()

            res_tg_id = resident.tg_id
            req_phone = request.phone

        await bot.send_message(
            user_id=res_tg_id,
            text=f"🎉 Заявка на регистрацию Вашего подрядчика ({req_phone}) одобрена! Для завершения регистрации подрядчика, перешлите "
            "подрядчику ссылку на бот, подрядчик должен ввести номер телефона, который вы указали для его регистрации.",
        )
        uid = event.callback.user.user_id
        if event.message and event.message.body:
            await bot.edit_message(
                message_id=event.message.body.mid,
                text="✅ Заявка одобрена!",
                attachments=None,
                parse_mode=ParseMode.HTML,
            )
        else:
            await bot.send_message(user_id=uid, text="✅ Заявка одобрена!")
        await send_user(bot, uid, "Меню регистрации:", get_registration_menu(), parse_mode=ParseMode.HTML)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "reject_rescont_request")
async def reject_resident_request_cb(event: MessageCallback, context: MemoryContext) -> None:
    try:
        if event.message:
            await event.message.answer(text="Введите причину отклонения:")
        else:
            await bot.send_message(
                user_id=event.callback.user.user_id,
                text="Введите причину отклонения:",
            )
        await context.set_state(RegistrationRequestStates.AWAIT_REJECT_RESIDENT_COMMENT)
        await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_created(RegistrationRequestStates.AWAIT_REJECT_RESIDENT_COMMENT)
async def process_reject_resident_comment(event: MessageCreated, context: MemoryContext) -> None:
    try:
        msg = text_from_message(event)
        if not msg:
            return
        data = await context.get_data()
        request_id = data["current_resident_request_id"]

        async with AsyncSessionLocal() as session:
            request = await session.get(ResidentContractorRequest, request_id)
            resident = await session.get(Resident, request.resident_id)
            request.status = "rejected"
            request.admin_comment = msg
            r_tg = resident.tg_id
            req_phone = request.phone
            await session.commit()

        await bot.send_message(
            user_id=r_tg,
            text=f"❌ Заявка на регистрацию Вашего подрядчика ({req_phone}) отклонена!\nПричина: {msg}",
        )
        uid = event.message.sender.user_id if event.message.sender else None
        await bot.send_message(user_id=uid, text="❌ Заявка отклонена!")
        await send_user(bot, uid, "Меню регистрации:", get_registration_menu(), parse_mode=ParseMode.HTML)
        await context.clear()
    except Exception as e:
        uid = event.message.sender.user_id if event.message.sender else RAZRAB
        await bot.send_message(user_id=RAZRAB, text=f"{uid} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "contractor_contractor_requests")
async def show_subcontractor_requests(event: MessageCallback) -> None:
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ContractorContractorRequest).filter(
                    ContractorContractorRequest.status == "pending"
                )
            )
            requests = result.scalars().all()
            b = InlineKeyboardBuilder()
            for req in requests:
                contractor = await session.get(Contractor, req.contractor_id)
                b.row(
                    CallbackButton(
                        text=f"{contractor.company}_{contractor.position}",
                        payload=f"view_subcontractor_request_{req.id}",
                    )
                )
            b.row(CallbackButton(text="⬅️ Назад", payload="registration_menu"))

            await edit_or_send_callback(
                bot,
                event,
                "Заявки на субподрядчиков от подрядчиков:",
                b,
                parse_mode=ParseMode.HTML,
            )
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload.startswith("view_subcontractor_request_"))
async def view_subcontractor_request(event: MessageCallback, context: MemoryContext) -> None:
    try:
        request_id = int(event.callback.payload.split("_")[-1])
        await context.update_data(current_subcontractor_request_id=request_id)

        async with AsyncSessionLocal() as session:
            request = await session.get(ContractorContractorRequest, request_id)
            contractor = await session.get(Contractor, request.contractor_id)

            con_plink = profile_link_line_html(
                contractor.first_name,
                contractor.last_name,
                contractor.tg_id,
                fallback_fio=contractor.fio,
            ).rstrip("\n")
            text = (
                f"📱 Телефон: {html_lib.escape(str(request.phone or ''))}\n"
                f"🏗 Виды работ: {html_lib.escape(str(request.work_types or ''))}\n"
                f"👤 Подрядчик: {html_lib.escape(str(contractor.company or ''))}_"
                f"{html_lib.escape(str(contractor.position or ''))}_"
                f"{fio_html(contractor.fio, contractor.tg_id)}\n"
                f"{con_plink}"
            )

            b = InlineKeyboardBuilder()
            b.row(CallbackButton(text="✅ Одобрить", payload="approve_subcontractor_request"))
            b.row(CallbackButton(text="❌ Отклонить", payload="reject_subcontractor_request"))

            await edit_or_send_callback(bot, event, text, b, parse_mode=ParseMode.HTML)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "approve_subcontractor_request")
async def approve_subcontractor_request(event: MessageCallback, context: MemoryContext) -> None:
    try:
        data = await context.get_data()
        request_id = data["current_subcontractor_request_id"]

        async with AsyncSessionLocal() as session:
            request = await session.get(ContractorContractorRequest, request_id)
            contractor = await session.get(Contractor, request.contractor_id)

            new_contractor = Contractor(
                phone=request.phone,
                work_types=request.work_types,
                affiliation=f"{contractor.id}_{contractor.company}_{contractor.position}_{contractor.fio}",
                status=False,
            )
            session.add(new_contractor)
            await session.commit()

            request.status = "approved"
            await session.commit()

            contr_tg_id = contractor.tg_id
            req_phone = request.phone

        await bot.send_message(
            user_id=contr_tg_id,
            text=f"🎉 Заявка на регистрацию Вашего субподрядчика ({req_phone}) одобрена! Для завершения регистрации субподрядчика, перешлите "
            "субподрядчику ссылку на бот, субподрядчик должен ввести номер телефона, который вы указали для его регистрации.",
        )
        uid = event.callback.user.user_id
        if event.message and event.message.body:
            await bot.edit_message(
                message_id=event.message.body.mid,
                text="✅ Заявка одобрена!",
                attachments=None,
                parse_mode=ParseMode.HTML,
            )
        else:
            await bot.send_message(user_id=uid, text="✅ Заявка одобрена!")
        await send_user(bot, uid, "Меню регистрации:", get_registration_menu(), parse_mode=ParseMode.HTML)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "reject_subcontractor_request")
async def reject_subcontractor_request_cb(event: MessageCallback, context: MemoryContext) -> None:
    try:
        if event.message:
            await event.message.answer(text="Введите причину отклонения:")
        else:
            await bot.send_message(
                user_id=event.callback.user.user_id,
                text="Введите причину отклонения:",
            )
        await context.set_state(RegistrationRequestStates.AWAIT_REJECT_SUBCONTRACTOR_COMMENT)
        await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_created(RegistrationRequestStates.AWAIT_REJECT_SUBCONTRACTOR_COMMENT)
async def process_reject_subcontractor_comment(event: MessageCreated, context: MemoryContext) -> None:
    try:
        msg = text_from_message(event)
        if not msg:
            return
        data = await context.get_data()
        request_id = data["current_subcontractor_request_id"]

        async with AsyncSessionLocal() as session:
            request = await session.get(ContractorContractorRequest, request_id)
            contractor = await session.get(Contractor, request.contractor_id)
            request.status = "rejected"
            request.admin_comment = msg
            c_tg = contractor.tg_id
            req_phone = request.phone
            await session.commit()

        await bot.send_message(
            user_id=c_tg,
            text=f"❌ Заявка на регистрацию Вашего субподрядчика ({req_phone}) отклонена!\nПричина: {msg}",
        )
        uid = event.message.sender.user_id if event.message.sender else None
        await bot.send_message(user_id=uid, text="❌ Заявка отклонена!")
        await send_user(bot, uid, "Меню регистрации:", get_registration_menu(), parse_mode=ParseMode.HTML)
        await context.clear()
    except Exception as e:
        uid = event.message.sender.user_id if event.message.sender else RAZRAB
        await bot.send_message(user_id=RAZRAB, text=f"{uid} - {e!s}")
        await asyncio.sleep(0.05)
