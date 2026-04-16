# handlers_admin_self_pass.py
import asyncio
import datetime
import html as html_lib

from maxapi import F, Router
from maxapi.context.base import BaseContext
from maxapi.context.state_machine import State, StatesGroup
from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from sqlalchemy import select

from bot import bot
from config import ADMIN_IDS, RAZRAB
from date_parser import parse_date
from db.models import AsyncSessionLocal, Manager, PermanentPass, TemporaryPass
from db.util import get_active_admins_managers_sb_tg_ids
from filters import IsAdminOrManager
from maxapi.enums.parse_mode import ParseMode
from temporary_truck import (
    PAYLOAD_PREFIX_SELF,
    category_from_truck_payload,
    truck_category_keyboard,
    vehicles_numbered_message_attachments,
)

from max_helpers import (
    answer_message,
    callback_ack,
    edit_or_send_callback,
    fio_html,
    main_menu_inline_button_kb,
    send_user,
    text_from_message,
)

router = Router(router_id="admin_self_pass")
router.filter(IsAdminOrManager())


class TemporarySelfPassStates(StatesGroup):
    CHOOSE_VEHICLE_TYPE = State()
    TRUCK_CHOOSE_CATEGORY = State()
    TRUCK_INPUT_BRAND = State()
    TRUCK_INPUT_NUMBER = State()
    TRUCK_INPUT_COMMENT = State()
    INPUT_CAR_NUMBER = State()
    INPUT_CAR_BRAND = State()
    INPUT_DESTINATION = State()
    INPUT_PURPOSE = State()
    INPUT_VISIT_DATE = State()
    INPUT_COMMENT = State()


class PermanentSelfPassStates(StatesGroup):
    INPUT_CAR_BRAND = State()
    INPUT_CAR_MODEL = State()
    INPUT_CAR_NUMBER = State()
    INPUT_DESTINATION = State()
    INPUT_CAR_OWNER = State()


def _passes_menu_kb() -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="Постоянные пропуска", payload="permanent_passes_menu"))
    b.row(CallbackButton(text="Временные пропуска", payload="temporary_passes_menu"))
    b.row(CallbackButton(text="Выписать временный пропуск", payload="issue_self_pass"))
    b.row(CallbackButton(text="Выписать постоянный пропуск", payload="issue_permanent_self_pass"))
    b.row(CallbackButton(text="⬅️ Назад", payload="back_to_main"))
    return b


async def get_owner_info_plain_and_html(user_id: int) -> tuple[str, str]:
    """Текст для БД (plain) и для сообщений в MAX (HTML, ФИО менеджера — ссылка на профиль)."""
    if user_id in ADMIN_IDS:
        s = "Администратор"
        return s, html_lib.escape(s)

    async with AsyncSessionLocal() as session:
        manager = await session.scalar(
            select(Manager).where(Manager.tg_id == user_id, Manager.status == True)  # noqa: E712
        )
        if manager and manager.fio:
            plain = f"Менеджер {manager.fio}"
            html = f"Менеджер {fio_html(manager.fio, manager.tg_id)}"
            return plain, html

    s = "Сотрудник"
    return s, html_lib.escape(s)


@router.message_callback(F.callback.payload == "issue_self_pass")
async def start_self_pass(event: MessageCallback, context: BaseContext) -> None:
    """Начало оформления временного пропуска для себя"""
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="Легковая", payload="self_vehicle_type_car"))
    kb.row(CallbackButton(text="Грузовая", payload="self_vehicle_type_truck"))
    await edit_or_send_callback(
        bot,
        event,
        (
            "❗️❗️❗️Внимание❗️❗️❗️\n"
            "Газели и фургоны с грузом до 3,5 тонн оформляются как легковая машина.\n"
            "Выберите тип машины:"
        ),
        kb,
    )
    await context.set_state(TemporarySelfPassStates.CHOOSE_VEHICLE_TYPE)


@router.message_callback(
    TemporarySelfPassStates.CHOOSE_VEHICLE_TYPE,
    F.callback.payload.startswith("self_vehicle_type_"),
)
async def process_self_vehicle_type(event: MessageCallback, context: BaseContext) -> None:
    """Обработка типа транспортного средства"""
    payload = event.callback.payload or ""
    vehicle_type = payload.split("_")[-1]
    await context.update_data(vehicle_type=vehicle_type)
    uid = event.callback.user.user_id

    if vehicle_type == "truck":
        kb = truck_category_keyboard(PAYLOAD_PREFIX_SELF)
        await bot.send_message(
            user_id=uid,
            text="Выберите тип машины:",
            attachments=vehicles_numbered_message_attachments(kb),
        )
        await context.set_state(TemporarySelfPassStates.TRUCK_CHOOSE_CATEGORY)
    else:
        await send_user(bot, uid, "Введите номер машины:")
        await context.set_state(TemporarySelfPassStates.INPUT_CAR_NUMBER)
    await callback_ack(bot, event)


@router.message_callback(
    TemporarySelfPassStates.TRUCK_CHOOSE_CATEGORY,
    F.callback.payload.startswith(f"{PAYLOAD_PREFIX_SELF}_"),
)
async def process_self_truck_category(event: MessageCallback, context: BaseContext) -> None:
    label = category_from_truck_payload(event.callback.payload or "", PAYLOAD_PREFIX_SELF)
    if not label:
        await callback_ack(bot, event)
        return
    await context.update_data(weight_category=label)
    await send_user(bot, event.callback.user.user_id, "Введите марку машины:")
    await context.set_state(TemporarySelfPassStates.TRUCK_INPUT_BRAND)
    await callback_ack(bot, event)


@router.message_created(F.message.body.text, TemporarySelfPassStates.TRUCK_INPUT_BRAND)
async def process_self_truck_brand(event: MessageCreated, context: BaseContext) -> None:
    try:
        t = text_from_message(event)
        if t is None:
            return
        await context.update_data(car_brand=t)
        await answer_message(event, "Введите номер машины:")
        await context.set_state(TemporarySelfPassStates.TRUCK_INPUT_NUMBER)
    except Exception as e:
        uid = event.message.sender.user_id if event.message.sender else 0
        await bot.send_message(user_id=RAZRAB, text=f"{uid} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, TemporarySelfPassStates.TRUCK_INPUT_NUMBER)
async def process_self_truck_number(event: MessageCreated, context: BaseContext) -> None:
    try:
        t = text_from_message(event)
        if t is None:
            return
        await context.update_data(car_number=t)
        await answer_message(event, "Добавьте комментарий (если не требуется, напишите 'нет'):")
        await context.set_state(TemporarySelfPassStates.TRUCK_INPUT_COMMENT)
    except Exception as e:
        uid = event.message.sender.user_id if event.message.sender else 0
        await bot.send_message(user_id=RAZRAB, text=f"{uid} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, TemporarySelfPassStates.TRUCK_INPUT_COMMENT)
async def process_self_truck_comment_save(event: MessageCreated, context: BaseContext) -> None:
    try:
        data = await context.get_data()
        t = text_from_message(event)
        if t is None:
            return
        comment = None if t.lower() == "нет" else t

        sender = event.message.sender
        if not sender:
            await answer_message(event, "❌ Не удалось определить пользователя.")
            await context.clear()
            return

        owner_plain, owner_html = await get_owner_info_plain_and_html(sender.user_id)
        visit_date = datetime.datetime.now().date()

        async with AsyncSessionLocal() as session:
            new_pass = TemporaryPass(
                owner_type="staff",
                vehicle_type="truck",
                weight_category=data.get("weight_category"),
                length_category=None,
                car_number=data["car_number"].upper(),
                car_brand=data["car_brand"],
                cargo_type=None,
                purpose="0",
                destination=None,
                visit_date=visit_date,
                owner_comment=comment,
                security_comment=f"Выписал {owner_plain}",
                status="approved",
                created_at=datetime.datetime.now(),
                time_registration=datetime.datetime.now(),
            )
            session.add(new_pass)
            await session.commit()

        await answer_message(
            event,
            f"✅ Временный пропуск на машину {data['car_number'].upper()} оформлен!",
            _passes_menu_kb(),
        )
        tg_ids = await get_active_admins_managers_sb_tg_ids()
        for tg_id in tg_ids:
            try:
                await send_user(
                    bot,
                    tg_id,
                    text=(
                        f"Пропуск от {owner_html} на машину с номером "
                        f"{html_lib.escape((data.get('car_number') or '').upper())} "
                        "одобрен автоматически.\n(Пропуска > Временные пропуска > Подтвержденные)"
                    ),
                    kb=main_menu_inline_button_kb(),
                    parse_mode=ParseMode.HTML,
                )
                await asyncio.sleep(0.05)
            except Exception:
                pass
        await context.clear()
    except Exception as e:
        await answer_message(event, f"❌ Ошибка при оформлении пропуска: {e!s}")
        await context.clear()


@router.message_created(F.message.body.text, TemporarySelfPassStates.INPUT_CAR_NUMBER)
async def process_car_number(event: MessageCreated, context: BaseContext) -> None:
    try:
        t = text_from_message(event)
        if t is None:
            return
        await context.update_data(car_number=t)
        await answer_message(event, "Введите марку машины:")
        await context.set_state(TemporarySelfPassStates.INPUT_CAR_BRAND)
    except Exception as e:
        uid = event.message.sender.user_id if event.message.sender else 0
        await bot.send_message(user_id=RAZRAB, text=f"{uid} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, TemporarySelfPassStates.INPUT_CAR_BRAND)
async def process_car_brand(event: MessageCreated, context: BaseContext) -> None:
    try:
        t = text_from_message(event)
        if t is None:
            return
        await context.update_data(car_brand=t)
        await answer_message(event, "Укажите пункт назначения(номер участка):")
        await context.set_state(TemporarySelfPassStates.INPUT_DESTINATION)
    except Exception as e:
        uid = event.message.sender.user_id if event.message.sender else 0
        await bot.send_message(user_id=RAZRAB, text=f"{uid} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, TemporarySelfPassStates.INPUT_DESTINATION)
async def process_self_purpose(event: MessageCreated, context: BaseContext) -> None:
    """Обработка цели визита"""
    t = text_from_message(event)
    if t is None:
        return
    await context.update_data(destination=t)
    await context.update_data(purpose="Не указано")
    await answer_message(
        event,
        "Введите дату приезда (в формате ДД.ММ, ДД.ММ.ГГГГ или '5 июня'):",
    )
    await context.set_state(TemporarySelfPassStates.INPUT_VISIT_DATE)


@router.message_created(F.message.body.text, TemporarySelfPassStates.INPUT_VISIT_DATE)
async def process_self_visit_date(event: MessageCreated, context: BaseContext) -> None:
    """Валидация и обработка даты визита"""
    raw = text_from_message(event)
    if raw is None:
        return
    visit_date = parse_date(raw)
    now = datetime.datetime.now().date()

    if not visit_date:
        await answer_message(event, "❌ Неверный формат даты! Введите снова:")
        return

    if visit_date < now:
        await answer_message(event, "❌ Дата не может быть меньше текущей! Введите снова:")
        return

    max_date = now + datetime.timedelta(days=31)
    if visit_date > max_date:
        await answer_message(event, "❌ Пропуск нельзя заказать на месяц вперед! Введите снова:")
        return

    await context.update_data(visit_date=visit_date)
    kb = InlineKeyboardBuilder()
    kb.row(CallbackButton(text="1", payload="days_0"))
    kb.row(
        CallbackButton(text="2", payload="days_1"),
        CallbackButton(text="7", payload="days_6"),
    )
    kb.row(
        CallbackButton(text="14", payload="days_13"),
        CallbackButton(text="30", payload="days_29"),
    )
    await answer_message(event, "Выберите кол-во дней действия пропуска:", kb)
    await context.set_state(TemporarySelfPassStates.INPUT_PURPOSE)


@router.message_callback(
    F.callback.payload.startswith("days_"),
    TemporarySelfPassStates.INPUT_PURPOSE,
)
async def process_days(event: MessageCallback, context: BaseContext) -> None:
    try:
        payload = event.callback.payload or ""
        days = int(payload.split("_")[1])
        await context.update_data(days=days)
        await send_user(bot, event.callback.user.user_id, "Добавьте комментарий (если не требуется, напишите 'нет'):")
        await context.set_state(TemporarySelfPassStates.INPUT_COMMENT)
        await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, TemporarySelfPassStates.INPUT_COMMENT)
async def process_self_comment_and_save(event: MessageCreated, context: BaseContext) -> None:
    """Сохранение временного пропуска с автоматическим подтверждением"""
    try:
        data = await context.get_data()
        t = text_from_message(event)
        if t is None:
            return
        comment = None if t.lower() == "нет" else t

        sender = event.message.sender
        if not sender:
            await answer_message(event, "❌ Не удалось определить пользователя.")
            await context.clear()
            return

        owner_plain, owner_html = await get_owner_info_plain_and_html(sender.user_id)

        async with AsyncSessionLocal() as session:
            new_pass = TemporaryPass(
                owner_type="staff",
                vehicle_type=data["vehicle_type"],
                weight_category=data.get("weight_category"),
                length_category=data.get("length_category"),
                car_number=data["car_number"].upper(),
                car_brand=data["car_brand"],
                cargo_type=data.get("cargo_type"),
                purpose=str(data.get("days")),
                destination=data["destination"],
                visit_date=data["visit_date"],
                owner_comment=comment,
                security_comment=f"Выписал {owner_plain}",
                status="approved",
                created_at=datetime.datetime.now(),
                time_registration=datetime.datetime.now(),
            )
            session.add(new_pass)
            await session.commit()

        await answer_message(
            event,
            f"✅ Временный пропуск на машину {data['car_number'].upper()} оформлен!",
            _passes_menu_kb(),
        )
        tg_ids = await get_active_admins_managers_sb_tg_ids()
        for tg_id in tg_ids:
            try:
                await send_user(
                    bot,
                    tg_id,
                    text=(
                        f"Пропуск от {owner_html} на машину с номером "
                        f"{html_lib.escape((data.get('car_number') or '').upper())} "
                        "одобрен автоматически.\n(Пропуска > Временные пропуска > Подтвержденные)"
                    ),
                    kb=main_menu_inline_button_kb(),
                    parse_mode=ParseMode.HTML,
                )
                await asyncio.sleep(0.05)
            except Exception:
                pass
        await context.clear()
    except Exception as e:
        await answer_message(event, f"❌ Ошибка при оформлении пропуска: {e!s}")
        await context.clear()


@router.message_callback(F.callback.payload == "issue_permanent_self_pass")
async def start_permanent_self_pass(event: MessageCallback, context: BaseContext) -> None:
    """Начало оформления постоянного пропуска для себя"""
    await send_user(bot, event.callback.user.user_id, "Введите марку машины:")
    await context.set_state(PermanentSelfPassStates.INPUT_CAR_BRAND)
    await callback_ack(bot, event)


@router.message_created(F.message.body.text, PermanentSelfPassStates.INPUT_CAR_BRAND)
async def process_self_car_brand(event: MessageCreated, context: BaseContext) -> None:
    t = text_from_message(event)
    if t is None:
        return
    await context.update_data(car_brand=t)
    await answer_message(event, "Введите модель машины:")
    await context.set_state(PermanentSelfPassStates.INPUT_CAR_MODEL)


@router.message_created(F.message.body.text, PermanentSelfPassStates.INPUT_CAR_MODEL)
async def process_self_car_model(event: MessageCreated, context: BaseContext) -> None:
    t = text_from_message(event)
    if t is None:
        return
    await context.update_data(car_model=t)
    await answer_message(event, "Введите номер машины:")
    await context.set_state(PermanentSelfPassStates.INPUT_CAR_NUMBER)


@router.message_created(F.message.body.text, PermanentSelfPassStates.INPUT_CAR_NUMBER)
async def process_self_car_number_perm(event: MessageCreated, context: BaseContext) -> None:
    t = text_from_message(event)
    if t is None:
        return
    await context.update_data(car_number=t)
    await answer_message(event, "Укажите пункт назначения(номер участка):")
    await context.set_state(PermanentSelfPassStates.INPUT_DESTINATION)


@router.message_created(F.message.body.text, PermanentSelfPassStates.INPUT_DESTINATION)
async def process_self_destination(event: MessageCreated, context: BaseContext) -> None:
    t = text_from_message(event)
    if t is None:
        return
    await context.update_data(destination=t)
    await answer_message(event, "Укажите владельца автомобиля:")
    await context.set_state(PermanentSelfPassStates.INPUT_CAR_OWNER)


@router.message_created(F.message.body.text, PermanentSelfPassStates.INPUT_CAR_OWNER)
async def process_self_car_owner(event: MessageCreated, context: BaseContext) -> None:
    try:
        data = await context.get_data()
        t = text_from_message(event)
        if t is None:
            return

        sender = event.message.sender
        if not sender:
            await answer_message(event, "❌ Не удалось определить пользователя.")
            await context.clear()
            return

        owner_plain, owner_html = await get_owner_info_plain_and_html(sender.user_id)

        async with AsyncSessionLocal() as session:
            new_pass = PermanentPass(
                car_brand=data["car_brand"],
                car_model=data["car_model"],
                car_number=data["car_number"].upper(),
                destination=data["destination"],
                car_owner=t,
                status="approved",
                security_comment=f"Выписал {owner_plain}",
                created_at=datetime.datetime.now(),
                time_registration=datetime.datetime.now(),
            )
            session.add(new_pass)
            await session.commit()

        tg_ids = await get_active_admins_managers_sb_tg_ids()
        for tg_id in tg_ids:
            try:
                await send_user(
                    bot,
                    tg_id,
                    text=(
                        f"Постоянный пропуск от {owner_html} на машину "
                        f"{html_lib.escape((data.get('car_number') or '').upper())} "
                        "одобрен автоматически."
                    ),
                    kb=main_menu_inline_button_kb(),
                    parse_mode=ParseMode.HTML,
                )
                await asyncio.sleep(0.05)
            except Exception:
                pass
        await context.clear()
    except Exception as e:
        await answer_message(event, f"❌ Ошибка при оформлении пропуска: {e!s}")
        await context.clear()
