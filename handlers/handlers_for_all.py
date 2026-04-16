import asyncio
import html as html_lib
from datetime import date, datetime
from io import BytesIO

from maxapi import F, Router
from maxapi.enums.parse_mode import ParseMode
from maxapi.context import MemoryContext
from maxapi.context.state_machine import State, StatesGroup
from maxapi.enums.upload_type import UploadType
from maxapi.filters.command import Command, CommandStart
from maxapi.types.input_media import InputMediaBuffer
from maxapi.types.updates.bot_started import BotStarted
from maxapi.types.updates.bot_stopped import BotStopped
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated
from maxapi.types.users import User
from openpyxl.workbook import Workbook
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot import bot
from config import ADMIN_IDS, RAZRAB
from db.models import (
    AsyncSessionLocal,
    Contractor,
    ContractorRegistrationRequest,
    Manager,
    RegistrationRequest,
    Resident,
    Security,
    TemporaryPass,
)
from db.util import (
    add_user_to_db,
    get_active_admins_and_managers_tg_ids,
    update_user_blocked,
    update_user_unblocked,
)
from temporary_truck import temporary_pass_valid_until_date

from handlers.handlers_admin_user_management import (
    get_admin_menu,
    get_manager_menu,
    is_valid_phone,
)
from handlers.handlers_security import get_security_menu
from keyboards import contractor_main_menu_kb, resident_main_kb
from max_helpers import (
    answer_message,
    callback_ack,
    edit_or_send_callback,
    fio_html,
    main_menu_inline_button_kb,
    profile_link_line_html,
    send_user,
    text_from_message,
    user_id_from_message,
)

router = Router(router_id="for_all")


@router.message_callback(F.callback.payload == "main_menu_inline")
async def main_menu_inline_callback(event: MessageCallback, context: MemoryContext) -> None:
    await context.clear()
    uid = event.callback.user.user_id
    if uid in ADMIN_IDS:
        await edit_or_send_callback(bot, event, "Добро пожаловать в Главное меню", get_admin_menu())
        return
    async with AsyncSessionLocal() as session:
        mgr = (
            await session.execute(
                select(Manager).where(Manager.tg_id == uid, Manager.status == True)  # noqa: E712
            )
        ).scalar()
        if mgr:
            await edit_or_send_callback(
                bot, event, "Добро пожаловать в Главное меню", get_manager_menu()
            )
            return
        sec = (
            await session.execute(
                select(Security).where(Security.tg_id == uid, Security.status == True)  # noqa: E712
            )
        ).scalar()
        if sec:
            await edit_or_send_callback(bot, event, "Добро пожаловать в меню СБ", get_security_menu())
            return
        res = (await session.execute(select(Resident).where(Resident.tg_id == uid))).scalar()
        if res:
            text = (
                f"👤 ФИО: {fio_html(res.fio, res.tg_id)}\n"
                f"{profile_link_line_html(res.first_name, res.last_name, res.tg_id, fallback_fio=res.fio)}"
                f"🏠 Номер участка: {html_lib.escape(str(res.plot_number or ''))}"
            )
            await edit_or_send_callback(
                bot, event, text, resident_main_kb, parse_mode=ParseMode.HTML
            )
            return
        con = (await session.execute(select(Contractor).where(Contractor.tg_id == uid))).scalar()
        if con:
            text = (
                f"ФИО: {fio_html(con.fio, con.tg_id)}\n"
                f"{profile_link_line_html(con.first_name, con.last_name, con.tg_id, fallback_fio=con.fio)}"
                f"Компания: {html_lib.escape(str(con.company or ''))}\n"
                f"Должность: {html_lib.escape(str(con.position or ''))}\n"
            )
            await edit_or_send_callback(
                bot,
                event,
                text,
                contractor_main_menu_kb(bool(con.can_add_contractor)),
                parse_mode=ParseMode.HTML,
            )
            return
    await callback_ack(bot, event)
    await bot.send_message(
        user_id=uid,
        text="Отправьте /start для входа или продолжите регистрацию по подсказкам выше.",
        attachments=[],
    )


class UserRegistration(StatesGroup):
    INPUT_FIO_CONTRACTOR = State()
    INPUT_COMPANY = State()
    INPUT_POSITION = State()
    INPUT_PHONE = State()
    INPUT_FIO = State()
    INPUT_PLOT = State()
    INPUT_PHOTO = State()
    INPUT_FIO_SECURITY_MANAGER = State()


async def _handle_exception(user_id: int, error: BaseException) -> None:
    await bot.send_message(user_id=RAZRAB, text=f"{user_id} - {error!s}")
    await asyncio.sleep(0.05)


@router.bot_stopped()
async def user_blocked_bot(event: BotStopped) -> None:
    await update_user_blocked(event.user.user_id)


@router.bot_started()
async def user_started_bot(event: BotStarted, context: MemoryContext) -> None:
    try:
        await update_user_unblocked(event.user.user_id)
        await run_command_start_flow(event.user, context)
    except Exception as e:
        await _handle_exception(event.user.user_id, e)


async def _get_existing_request(model, tg_id: int):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(model)
            .filter(model.tg_id == tg_id)
            .order_by(model.created_at.desc())
        )
        return result.scalars().first()


async def check_phone_in_tables(phone: str):
    async with AsyncSessionLocal() as session:
        for model, user_type in [
            (Manager, "manager"),
            (Security, "security"),
            (Resident, "resident"),
            (Contractor, "contractor"),
        ]:
            result = await session.execute(select(model).filter(model.phone == phone))
            if user := result.scalars().first():
                return (user_type, user)
    return (None, None)


async def attach_max_profile_to_db_user(user_type: str, user_db_id: int, sender: User) -> None:
    """Записывает id и профиль из MAX в строку роли (после миграции с Telegram или сброса tg_id)."""
    model = {"manager": Manager, "security": Security, "resident": Resident, "contractor": Contractor}[
        user_type
    ]
    async with AsyncSessionLocal() as session:
        row = await session.get(model, user_db_id)
        if not row:
            return
        row.tg_id = sender.user_id
        row.username = sender.username
        row.first_name = sender.first_name
        row.last_name = sender.last_name
        row.time_registration = datetime.now()
        await session.commit()


async def update_user_data(user_type: str, user_db_id: int, tg_user: User, fio: str) -> None:
    async with AsyncSessionLocal() as session:
        if user_type == "manager":
            user_db = await session.get(Manager, user_db_id)
        else:
            user_db = await session.get(Security, user_db_id)

        if not user_db:
            return

        user_db.tg_id = tg_user.user_id
        user_db.username = tg_user.username
        user_db.first_name = tg_user.first_name
        user_db.last_name = tg_user.last_name
        user_db.time_registration = datetime.now()
        user_db.status = True
        user_db.fio = fio

        session.add(user_db)
        await session.commit()

        role_name = "менеджер" if user_type == "manager" else "сотрудник СБ"
        tg_ids = await get_active_admins_and_managers_tg_ids()

        for tg_id in tg_ids:
            try:
                await bot.send_message(
                    user_id=tg_id,
                    text=(
                        f"Зарегистрирован новый {html_lib.escape(role_name)}: "
                        f"{fio_html(fio, tg_user.user_id)}"
                    ),
                    parse_mode=ParseMode.HTML,
                    attachments=[main_menu_inline_button_kb().as_markup()],
                )
                await asyncio.sleep(0.05)
            except Exception:
                pass


async def run_command_start_flow(sender: User, context: MemoryContext) -> None:
    uid = sender.user_id
    await add_user_to_db(
        uid,
        sender.username,
        sender.first_name,
        sender.last_name,
        datetime.now(),
    )

    if uid in ADMIN_IDS:
        await context.clear()
        await bot.send_message(
            user_id=uid,
            text="Здравствуйте!\n\nДобро пожаловать в Главное меню",
            attachments=[get_admin_menu().as_markup()],
        )
        return

    async with AsyncSessionLocal() as session:
        mgr = (
            await session.execute(
                select(Manager).where(Manager.tg_id == uid, Manager.status == True)  # noqa: E712
            )
        ).scalar()
        if mgr:
            await context.clear()
            kb = get_admin_menu() if uid in ADMIN_IDS else get_manager_menu()
            await bot.send_message(
                user_id=uid,
                text="Добро пожаловать в Главное меню",
                attachments=[kb.as_markup()],
            )
            return
        sec = (
            await session.execute(
                select(Security).where(Security.tg_id == uid, Security.status == True)  # noqa: E712
            )
        ).scalar()
        if sec:
            await context.clear()
            await bot.send_message(
                user_id=uid,
                text="Добро пожаловать в меню СБ",
                attachments=[get_security_menu().as_markup()],
            )
            return
        res = (
            await session.execute(
                select(Resident).where(Resident.tg_id == uid, Resident.status == True)  # noqa: E712
            )
        ).scalar()
        if res:
            await context.clear()
            text = (
                "Добро пожаловать в личный кабинет резидента!\n\n"
                f"👤 ФИО: {fio_html(res.fio, res.tg_id)}\n"
                f"{profile_link_line_html(res.first_name, res.last_name, res.tg_id, fallback_fio=res.fio)}"
                f"🏠 Номер участка: {html_lib.escape(str(res.plot_number or ''))}"
            )
            await bot.send_message(
                user_id=uid,
                text=text,
                attachments=[resident_main_kb.as_markup()],
                parse_mode=ParseMode.HTML,
            )
            return
        con = (
            await session.execute(
                select(Contractor).where(Contractor.tg_id == uid, Contractor.status == True)  # noqa: E712
            )
        ).scalar()
        if con:
            await context.clear()
            text = (
                "Добро пожаловать в личный кабинет подрядчика!\n\n"
                f"ФИО: {fio_html(con.fio, con.tg_id)}\n"
                f"{profile_link_line_html(con.first_name, con.last_name, con.tg_id, fallback_fio=con.fio)}"
                f"Компания: {html_lib.escape(str(con.company or ''))}\n"
                f"Должность: {html_lib.escape(str(con.position or ''))}\n"
            )
            kb = contractor_main_menu_kb(bool(con.can_add_contractor))
            await bot.send_message(
                user_id=uid,
                text=text,
                attachments=[kb.as_markup()],
                parse_mode=ParseMode.HTML,
            )
            return

    resident_request = await _get_existing_request(RegistrationRequest, uid)
    contractor_request = await _get_existing_request(ContractorRegistrationRequest, uid)
    request = resident_request or contractor_request

    if request:
        if request.status == "pending":
            await bot.send_message(user_id=uid, text="⏳ Ваша заявка находится в обработке", attachments=[])
        elif request.status == "rejected":
            await context.set_state(UserRegistration.INPUT_PHONE)
            text = f"❌ Ваша заявка отклонена. Причина: {request.admin_comment}\n\n"
            await bot.send_message(
                user_id=uid,
                text=text + "Введите номер телефона для повторной регистрации:",
                attachments=[],
            )
        elif request.status == "approved":
            async with AsyncSessionLocal() as session:
                if resident_request:
                    res = await session.get(Resident, request.resident_id)
                    if res:
                        text = (
                            "✅ Ваша заявка одобрена! Добро пожаловать в личный кабинет резидента!\n\n"
                            f"👤 ФИО: {fio_html(res.fio, res.tg_id)}\n"
                            f"{profile_link_line_html(res.first_name, res.last_name, res.tg_id, fallback_fio=res.fio)}"
                            f"🏠 Номер участка: {html_lib.escape(str(res.plot_number or ''))}"
                        )
                        await bot.send_message(
                            user_id=uid,
                            text=text,
                            attachments=[resident_main_kb.as_markup()],
                            parse_mode=ParseMode.HTML,
                        )
                else:
                    contr = await session.get(Contractor, request.contractor_id)
                    if contr:
                        text = (
                            "✅ Ваша заявка одобрена! Добро пожаловать в личный кабинет подрядчика!\n\n"
                            f"ФИО: {fio_html(contr.fio, contr.tg_id)}\n"
                            f"{profile_link_line_html(contr.first_name, contr.last_name, contr.tg_id, fallback_fio=contr.fio)}"
                            f"Компания: {html_lib.escape(str(contr.company or ''))}\n"
                            f"Должность: {html_lib.escape(str(contr.position or ''))}\n"
                        )
                        kb = contractor_main_menu_kb(bool(contr.can_add_contractor))
                        await bot.send_message(
                            user_id=uid,
                            text=text,
                            attachments=[kb.as_markup()],
                            parse_mode=ParseMode.HTML,
                        )
        return

    await context.set_state(UserRegistration.INPUT_PHONE)
    await bot.send_message(
        user_id=uid,
        text="Введите номер телефона в формате 8XXXXXXXXXX:",
        attachments=[],
    )


@router.message_created(CommandStart())
async def process_start_user(event: MessageCreated, context: MemoryContext) -> None:
    try:
        s = event.message.sender
        if not s:
            return
        await run_command_start_flow(s, context)
    except Exception as e:
        uid = event.message.sender.user_id if event.message.sender else RAZRAB
        await _handle_exception(uid, e)


@router.message_created(UserRegistration.INPUT_PHONE)
async def process_phone_input(event: MessageCreated, context: MemoryContext) -> None:
    try:
        phone = text_from_message(event)
        if not phone:
            return
        uid = user_id_from_message(event)
        if uid is None:
            return

        if not is_valid_phone(phone):
            await event.message.answer(
                text="Телефон должен быть в формате 8XXXXXXXXXX.\nПопробуйте ввести еще раз!"
            )
            return

        user_type, user_db = await check_phone_in_tables(phone)

        if user_type is None or user_db is None:
            await event.message.answer(text="Номер не найден в системе. Введите телефон еще раз.")
            return

        if user_db.tg_id is not None and user_db.tg_id != uid:
            await event.message.answer(
                text="Этот номер уже привязан к другому аккаунту MAX. Если это ошибка, обратитесь в управляющую компанию."
            )
            return

        s = event.message.sender
        if not s:
            return

        # Миграция с Telegram / сброс tg_id: активная запись в БД — привязываем MAX без повторной регистрации
        if user_type == "manager" and user_db.status:
            await attach_max_profile_to_db_user("manager", user_db.id, s)
            await context.clear()
            kb = get_admin_menu() if uid in ADMIN_IDS else get_manager_menu()
            await answer_message(
                event,
                text="Вход выполнен. Добро пожаловать в Главное меню!",
                kb=kb,
            )
            return

        if user_type == "security" and user_db.status:
            await attach_max_profile_to_db_user("security", user_db.id, s)
            await context.clear()
            await answer_message(
                event,
                text="Вход выполнен. Добро пожаловать в меню СБ!",
                kb=get_security_menu(),
            )
            return

        if user_type == "resident" and user_db.status:
            await attach_max_profile_to_db_user("resident", user_db.id, s)
            async with AsyncSessionLocal() as session:
                res = await session.get(Resident, user_db.id)
            await context.clear()
            if not res:
                return
            text = (
                "Добро пожаловать в личный кабинет резидента!\n\n"
                f"👤 ФИО: {fio_html(res.fio, res.tg_id)}\n"
                f"{profile_link_line_html(res.first_name, res.last_name, res.tg_id, fallback_fio=res.fio)}"
                f"🏠 Номер участка: {html_lib.escape(str(res.plot_number or ''))}"
            )
            await answer_message(
                event, text=text, kb=resident_main_kb, parse_mode=ParseMode.HTML
            )
            return

        if user_type == "contractor" and user_db.status:
            await attach_max_profile_to_db_user("contractor", user_db.id, s)
            async with AsyncSessionLocal() as session:
                con = await session.get(Contractor, user_db.id)
            await context.clear()
            if not con:
                return
            text = (
                "Добро пожаловать в личный кабинет подрядчика!\n\n"
                f"ФИО: {fio_html(con.fio, con.tg_id)}\n"
                f"{profile_link_line_html(con.first_name, con.last_name, con.tg_id, fallback_fio=con.fio)}"
                f"Компания: {html_lib.escape(str(con.company or ''))}\n"
                f"Должность: {html_lib.escape(str(con.position or ''))}\n"
            )
            await answer_message(
                event,
                text=text,
                kb=contractor_main_menu_kb(bool(con.can_add_contractor)),
                parse_mode=ParseMode.HTML,
            )
            return

        data: dict = {"phone": phone}

        if user_type in ["manager", "security"]:
            data.update(user_type=user_type, user_db_id=user_db.id)
            await context.set_data(data)
            await event.message.answer(text="Введите ваше ФИО:")
            await context.set_state(UserRegistration.INPUT_FIO_SECURITY_MANAGER)
            return

        async def _check_existing(model, status_field):
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(model).filter(
                        getattr(model, status_field) == user_db.id,
                        model.status.in_(["pending", "rejected"]),
                    )
                )
                return result.scalars().first()

        if user_type == "resident":
            if existing_request := await _check_existing(RegistrationRequest, "resident_id"):
                await context.clear()
                if existing_request.status == "pending":
                    await event.message.answer(text="Ваша заявка находится в обработке")
                    return
            data["resident_id"] = user_db.id
            if user_db.fio:
                next_state = UserRegistration.INPUT_PLOT
                prompt = "Введите номер участка:"
            else:
                next_state = UserRegistration.INPUT_FIO
                prompt = "Введите ФИО:"

        elif user_type == "contractor":
            if existing_request := await _check_existing(ContractorRegistrationRequest, "contractor_id"):
                await context.clear()
                if existing_request.status == "pending":
                    await event.message.answer(text="Ваша заявка находится в обработке")
                    return
            data["contractor_id"] = user_db.id
            next_state = UserRegistration.INPUT_FIO_CONTRACTOR
            prompt = "Введите ФИО:"

        else:
            await event.message.answer(text="Номер не найден в системе. Введите телефон еще раз.")
            return

        await context.set_data(data)
        await event.message.answer(text=prompt)
        await context.set_state(next_state)

    except Exception as e:
        uid = user_id_from_message(event) or RAZRAB
        await _handle_exception(uid, e)


@router.message_created(UserRegistration.INPUT_FIO)
async def process_fio_input(event: MessageCreated, context: MemoryContext) -> None:
    try:
        msg = text_from_message(event)
        if not msg:
            return
        await context.update_data(fio=msg)
        await event.message.answer(text="Введите номер участка:")
        await context.set_state(UserRegistration.INPUT_PLOT)
    except Exception as e:
        uid = user_id_from_message(event) or RAZRAB
        await _handle_exception(uid, e)


@router.message_created(UserRegistration.INPUT_PLOT)
async def process_plot_input(event: MessageCreated, context: MemoryContext) -> None:
    try:
        data = await context.get_data()
        plot_number = text_from_message(event)
        if not plot_number:
            return
        s = event.message.sender
        if not s:
            return

        async with AsyncSessionLocal() as session:
            if data.get("fio"):
                new_request = RegistrationRequest(
                    resident_id=data["resident_id"],
                    fio=data["fio"],
                    tg_id=s.user_id,
                    username=s.username,
                    first_name=s.first_name,
                    last_name=s.last_name,
                    plot_number=plot_number,
                )
            else:
                resident = await session.get(Resident, data["resident_id"])
                new_request = RegistrationRequest(
                    resident_id=data["resident_id"],
                    fio=resident.fio,
                    tg_id=s.user_id,
                    username=s.username,
                    first_name=s.first_name,
                    last_name=s.last_name,
                    plot_number=plot_number,
                )
            session.add(new_request)
            await session.commit()

        await event.message.answer(text="Заявка отправлена на модерацию")
        tg_ids = await get_active_admins_and_managers_tg_ids()
        for tg_id in tg_ids:
            try:
                await send_user(
                    bot,
                    tg_id,
                    text="Поступила заявка на регистрацию резидента (Регистрация > Регистрация резидентов",
                    kb=main_menu_inline_button_kb(),
                    main_menu_attachment=True,
                )
                await asyncio.sleep(0.05)
            except Exception:
                pass
        await context.clear()
    except Exception as e:
        uid = user_id_from_message(event) or RAZRAB
        await _handle_exception(uid, e)


@router.message_created(UserRegistration.INPUT_FIO_CONTRACTOR)
async def process_contractor_fio(event: MessageCreated, context: MemoryContext) -> None:
    try:
        msg = text_from_message(event)
        if not msg:
            return
        await context.update_data(fio=msg)
        await event.message.answer(text="Введите название компании:")
        await context.set_state(UserRegistration.INPUT_COMPANY)
    except Exception as e:
        uid = user_id_from_message(event) or RAZRAB
        await _handle_exception(uid, e)


@router.message_created(UserRegistration.INPUT_COMPANY)
async def process_company(event: MessageCreated, context: MemoryContext) -> None:
    try:
        msg = text_from_message(event)
        if not msg:
            return
        await context.update_data(company=msg)
        await event.message.answer(text="Введите вашу должность:")
        await context.set_state(UserRegistration.INPUT_POSITION)
    except Exception as e:
        uid = user_id_from_message(event) or RAZRAB
        await _handle_exception(uid, e)


@router.message_created(UserRegistration.INPUT_POSITION)
async def process_position(event: MessageCreated, context: MemoryContext) -> None:
    try:
        msg = text_from_message(event)
        if not msg:
            return
        await context.update_data(position=msg)
        data = await context.get_data()
        s = event.message.sender
        if not s:
            return

        async with AsyncSessionLocal() as session:
            new_request = ContractorRegistrationRequest(
                contractor_id=data["contractor_id"],
                fio=data["fio"],
                company=data["company"],
                position=data["position"],
                tg_id=s.user_id,
                username=s.username,
                first_name=s.first_name,
                last_name=s.last_name,
            )
            session.add(new_request)
            await session.commit()

        await event.message.answer(text="Заявка отправлена на модерацию!")
        tg_ids = await get_active_admins_and_managers_tg_ids()
        for tg_id in tg_ids:
            try:
                await send_user(
                    bot,
                    tg_id,
                    text="Поступила заявка на регистрацию подрядчика (Регистрация > Регистрация подрядчика",
                    kb=main_menu_inline_button_kb(),
                    main_menu_attachment=True,
                )
                await asyncio.sleep(0.05)
            except Exception:
                pass
        await context.clear()
    except Exception as e:
        uid = user_id_from_message(event) or RAZRAB
        await _handle_exception(uid, e)


@router.message_callback(F.callback.payload == "restart")
async def restart_application(event: MessageCallback, context: MemoryContext) -> None:
    try:
        uid = event.callback.user.user_id
        await context.set_state(UserRegistration.INPUT_PHONE)
        await bot.send_message(user_id=uid, text="Введите номер телефона:")
        await callback_ack(bot, event)
    except Exception as e:
        await _handle_exception(event.callback.user.user_id, e)


@router.message_created(UserRegistration.INPUT_FIO_SECURITY_MANAGER)
async def process_fio_security_manager(event: MessageCreated, context: MemoryContext) -> None:
    try:
        data = await context.get_data()
        msg = text_from_message(event)
        if not msg:
            return
        fio = msg
        user_type = data["user_type"]
        user_db_id = data["user_db_id"]
        s = event.message.sender
        if not s:
            return

        await update_user_data(user_type, user_db_id, s, fio)

        reply_text = (
            {
                "manager": "Регистрация менеджера завершена! Добро пожаловать!",
                "security": "Регистрация сотрудника СБ завершена! Добро пожаловать!",
            }[user_type]
            + " Для продолжения работы нажмите кнопку «Главное меню» или меню ниже."
        )

        if user_type == "manager":
            kb = (
                get_admin_menu()
                if s.user_id in ADMIN_IDS
                else get_manager_menu()
            )
        else:
            kb = get_security_menu()

        await answer_message(event, text=reply_text, kb=kb)

        await context.clear()
    except Exception as e:
        uid = user_id_from_message(event) or RAZRAB
        await _handle_exception(uid, e)


@router.message_created(Command("excel"))
async def export_temporary_passes_to_excel(event: MessageCreated) -> None:
    uid = user_id_from_message(event)
    if uid != 5590779:
        return

    try:
        async with AsyncSessionLocal() as session:
            stmt = select(TemporaryPass).options(
                selectinload(TemporaryPass.resident),
                selectinload(TemporaryPass.contractor),
            )
            result = await session.execute(stmt)
            today = date.today()
            passes = [
                p
                for p in result.scalars().all()
                if (u := temporary_pass_valid_until_date(p)) is not None
                and u >= today
            ]

        if not passes:
            await event.message.answer(text="📊 Нет данных о временных пропусках для выгрузки")
            return

        wb = Workbook()
        ws = wb.active
        ws.title = "Временные пропуска"

        headers = [
            "ФИО",
            "Тип владельца",
            "Тип ТС",
            "Весовая категория",
            "Длинная категория",
            "Номер машины",
            "Марка машины",
            "Тип груза",
            "Цель визита",
            "Дата визита",
            "Комментарий владельца",
            "Комментарий резидента",
            "Комментарий СБ",
            "Статус",
            "Направление",
            "Дата создания",
            "Время регистрации",
        ]

        ws.append(headers)

        for pass_item in passes:
            if pass_item.owner_type == "resident" and pass_item.resident:
                fio = pass_item.resident.fio or "Не указано"
            elif pass_item.owner_type == "contractor" and pass_item.contractor:
                fio = pass_item.contractor.fio or "Не указано"
            else:
                fio = "Неизвестно"

            row = [
                fio,
                "Резидент" if pass_item.owner_type == "resident" else "Подрядчик",
                "Легковой" if pass_item.vehicle_type == "car" else "Грузовой",
                pass_item.weight_category or "",
                pass_item.length_category or "",
                pass_item.car_number,
                pass_item.car_brand,
                pass_item.cargo_type or "",
                pass_item.purpose,
                pass_item.visit_date.strftime("%Y-%m-%d") if pass_item.visit_date else "",
                pass_item.owner_comment or "",
                pass_item.resident_comment or "",
                pass_item.security_comment or "",
                pass_item.status,
                pass_item.destination or "",
                pass_item.created_at.strftime("%Y-%m-%d %H:%M:%S") if pass_item.created_at else "",
                pass_item.time_registration.strftime("%Y-%m-%d %H:%M:%S")
                if pass_item.time_registration
                else "",
            ]
            ws.append(row)

        for column in ws.columns:
            max_length = 0
            column_letter = column[0].column_letter
            for cell in column:
                try:
                    if len(str(cell.value)) > max_length:
                        max_length = len(str(cell.value))
                except Exception:
                    pass
            adjusted_width = max_length + 2
            ws.column_dimensions[column_letter].width = adjusted_width

        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        fname = f"temporary_passes_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

        await bot.send_message(
            user_id=uid,
            text=f"📊 Выгрузка временных пропусков ({len(passes)} записей)",
            attachments=[
                InputMediaBuffer(
                    buffer.getvalue(),
                    filename=fname,
                    type=UploadType.FILE,
                )
            ],
        )

    except Exception as e:
        await event.message.answer(text=f"❌ Произошла ошибка при выгрузке: {e!s}")
        print(f"Error exporting Excel: {e}")
