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
import datetime
import logging
import random

from maxapi import F, Router
from sqlalchemy import select, func, or_
from truck_yookassa_flow import (
    NewTruckPassPaymentForm,
    create_awaiting_payment_truck_pass,
    send_truck_payment_message,
)

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
from config import PAGE_SIZE, RAZRAB, PASS_TIME, MAX_CAR_PASSES, MAX_TRUCK_PASSES
from keyboards import contractor_main_menu_kb
from date_parser import parse_date
from db.models import Resident, AsyncSessionLocal, ResidentContractorRequest, PermanentPass, Contractor, TemporaryPass, \
    ContractorContractorRequest
from db.util import get_active_admins_and_managers_tg_ids, get_active_admins_managers_sb_tg_ids, text_warning
from filters import IsResident, IsContractor
from handlers.handlers_admin_user_management import is_valid_phone
from temporary_truck import (
    PAYLOAD_PREFIX_RC,
    category_from_truck_payload,
    is_new_truck_pass,
    temp_pass_duration_label,
    truck_category_keyboard,
    vehicles_numbered_message_attachments,
)

router = Router(router_id="contractor")
router.filter(IsContractor())

logger = logging.getLogger(__name__)


def _temporary_pass_followup_keyboard():
    return inline_kb(
        [
            [CallbackButton(text="Оформить временный пропуск", payload="create_temporary_pass")],
            [CallbackButton(text="Назад", payload="back_to_main_menu")],
        ]
    )


async def _notify_contractor_temp_pass_auto_approved_delayed(
    user_id: int,
    delay_sec: int,
    car_number: str,
    company: str,
    position: str,
    fio: str,
) -> None:
    try:
        await asyncio.sleep(delay_sec)
        kb = _temporary_pass_followup_keyboard()
        await bot.send_message(
            user_id=user_id,
            text=f"✅ Ваш временный пропуск одобрен на машину с номером {car_number}",
            attachments=[kb.as_markup()],
        )
        await bot.send_message(user_id=user_id, text=text_warning)
        for tg_id in await get_active_admins_managers_sb_tg_ids():
            try:
                await bot.send_message(
                    user_id=tg_id,
                    text=(
                        f"Пропуск от подрядчика {fio_html(fio, user_id)}, "
                        f"{html_lib.escape(company)} — {html_lib.escape(position)}, "
                        f"на машину с номером {html_lib.escape(car_number)} одобрен автоматически."
                    ),
                    parse_mode=ParseMode.HTML,
                    attachments=[main_menu_inline_button_kb().as_markup()],
                )
                await asyncio.sleep(0.05)
            except Exception:
                pass
    except Exception:
        logger.exception("Ошибка в отложенном автоодобрении временного пропуска (подрядчик)")


def _uid(ev: MessageCreated | MessageCallback) -> int | None:
    if isinstance(ev, MessageCallback):
        return ev.callback.user.user_id
    return user_id_from_message(ev)


class ContractorContractorRegistration(StatesGroup):
    INPUT_PHONE = State()
    INPUT_WORK_TYPES = State()


class TemporaryPassViewStates(StatesGroup):
    VIEWING_PENDING = State()
    VIEWING_APPROVED = State()
    VIEWING_REJECTED = State()


class TemporaryPassStates(StatesGroup):
    CHOOSE_VEHICLE_TYPE = State()
    CHOOSE_WEIGHT_CATEGORY = State()
    CHOOSE_LENGTH_CATEGORY = State()
    CHOOSE_TRUCK_CATEGORY = State()
    INPUT_TRUCK_BRAND = State()
    INPUT_TRUCK_NUMBER = State()
    INPUT_TRUCK_COMMENT = State()
    INPUT_TRUCK_VISIT_DATE = State()
    INPUT_CAR_NUMBER = State()
    INPUT_CAR_BRAND = State()
    INPUT_CARGO_TYPE = State()
    INPUT_DESTINATION = State()
    INPUT_PURPOSE = State()
    INPUT_VISIT_DATE = State()
    INPUT_COMMENT = State()



@router.message_created(Command("start"))
async def contractor_start(event: MessageCreated):
    """Обработчик команды /start для подрядчика"""
    try:
        uid = user_id_from_message(event)
        if uid is None:
            return
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Contractor).where(Contractor.tg_id == uid)
            )
            contractor = result.scalar()
        if not contractor:
            return await answer_message(event, "❌ Профиль не найден")
        text = (
            "<b>Добро пожаловать в личный кабинет подрядчика!</b>\n\n"
            f"ФИО: {fio_html(contractor.fio, contractor.tg_id)}\n"
            f"{profile_link_line_html(contractor.first_name, contractor.last_name, contractor.tg_id, fallback_fio=contractor.fio)}"
            f"Компания: {html_lib.escape(str(contractor.company or ''))}\n"
            f"Должность: {html_lib.escape(str(contractor.position or ''))}\n"
        )
        await answer_message(
            event,
            text=text,
            kb=contractor_main_menu_kb(bool(contractor.can_add_contractor)),
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text == "Главное меню")
async def main_menu(event: MessageCreated):
    try:
        """Обработчик главного меню резидента"""
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Contractor)
                .where(Contractor.tg_id == user_id_from_message(event))
            )
            contractor = result.scalar()

            if not contractor:
                return await answer_message(event, "❌ Профиль не найден")

            text = (
                f"ФИО: {fio_html(contractor.fio, contractor.tg_id)}\n"
                f"{profile_link_line_html(contractor.first_name, contractor.last_name, contractor.tg_id, fallback_fio=contractor.fio)}"
                f"Компания: {html_lib.escape(str(contractor.company or ''))}\n"
                f"Должность: {html_lib.escape(str(contractor.position or ''))}\n"
            )
            menu_kb = contractor_main_menu_kb(bool(contractor.can_add_contractor))

            await answer_message(event, text=text, kb=menu_kb, parse_mode=ParseMode.HTML)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "back_to_main_menu")
async def main_menu(event: MessageCallback, context: BaseContext):
    try:
        await context.clear()
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Contractor)
                .where(Contractor.tg_id == event.callback.user.user_id)
            )
            contractor = result.scalar()

            if not contractor:
                return await event.message.answer("❌ Профиль не найден")

            text = (
                f"ФИО: {fio_html(contractor.fio, contractor.tg_id)}\n"
                f"{profile_link_line_html(contractor.first_name, contractor.last_name, contractor.tg_id, fallback_fio=contractor.fio)}"
                f"Компания: {html_lib.escape(str(contractor.company or ''))}\n"
                f"Должность: {html_lib.escape(str(contractor.position or ''))}\n"
            )

            menu_kb = contractor_main_menu_kb(bool(contractor.can_add_contractor))

            await edit_or_send_callback(bot, event, text, menu_kb, parse_mode=ParseMode.HTML)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "temporary_pass_menu")
async def temporary_pass_menu(event: MessageCallback):
    try:
        keyboard = inline_kb([
            [CallbackButton(text="Оформить временный пропуск", payload="create_temporary_pass")],
            [CallbackButton(text="На подтверждении", payload="my_pending_temp_passes")],
            [CallbackButton(text="Подтвержденные", payload="my_approved_temp_passes")],
            [CallbackButton(text="Отклоненные", payload="my_rejected_temp_passes")],
            [CallbackButton(text="Назад", payload="back_to_main_menu")]
        ])
        await event.message.answer(text="Временные пропуска", attachments=[keyboard.as_markup()])
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "create_temporary_pass")
async def start_temporary_pass(event: MessageCallback, context: BaseContext):
    try:
        keyboard = inline_kb([
            [CallbackButton(text="Легковая", payload="vehicle_type_car")],
            [CallbackButton(text="Грузовая", payload="vehicle_type_truck")]
        ])
        await event.message.answer(
            text=(
                "❗️❗️❗️Внимание❗️❗️❗️\n"
                "Газели и фургоны с грузом до 3,5 тонн оформляются как легковая машина.\n"
                "Выберите тип машины:"
            ),
            attachments=[keyboard.as_markup()],
        )
        await context.set_state(TemporaryPassStates.CHOOSE_VEHICLE_TYPE)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(TemporaryPassStates.CHOOSE_VEHICLE_TYPE, F.callback.payload.startswith("vehicle_type_"))
async def process_vehicle_type(event: MessageCallback, context: BaseContext):
    try:
        vehicle_type = event.callback.payload.split("_")[-1]
        await context.update_data(vehicle_type=vehicle_type)

        if vehicle_type == "truck":
            kb = truck_category_keyboard(PAYLOAD_PREFIX_RC)
            await event.message.answer(
                text="Выберите тип машины:",
                attachments=vehicles_numbered_message_attachments(kb),
            )
            await context.set_state(TemporaryPassStates.CHOOSE_TRUCK_CATEGORY)
        else:
            # Для легковых сразу переходим к номеру
            await event.message.answer("Введите номер машины:")
            await context.set_state(TemporaryPassStates.INPUT_CAR_NUMBER)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(
    TemporaryPassStates.CHOOSE_TRUCK_CATEGORY,
    F.callback.payload.startswith(f"{PAYLOAD_PREFIX_RC}_"),
)
async def process_truck_category_contractor(event: MessageCallback, context: BaseContext):
    try:
        label = category_from_truck_payload(event.callback.payload or "", PAYLOAD_PREFIX_RC)
        if not label:
            return
        await context.update_data(weight_category=label)
        await event.message.answer(text="Введите марку машины:")
        await context.set_state(TemporaryPassStates.INPUT_TRUCK_BRAND)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, TemporaryPassStates.INPUT_TRUCK_BRAND)
async def process_truck_brand_contractor(event: MessageCreated, context: BaseContext):
    try:
        await context.update_data(car_brand=(text_from_message(event) or ''))
        await answer_message(event, "Введите номер машины:")
        await context.set_state(TemporaryPassStates.INPUT_TRUCK_NUMBER)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, TemporaryPassStates.INPUT_TRUCK_NUMBER)
async def process_truck_number_contractor(event: MessageCreated, context: BaseContext):
    try:
        await context.update_data(car_number=(text_from_message(event) or ''))
        await answer_message(event, "Добавьте комментарий (если не требуется, напишите 'нет'):")
        await context.set_state(TemporaryPassStates.INPUT_TRUCK_COMMENT)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, TemporaryPassStates.INPUT_TRUCK_COMMENT)
async def process_truck_comment_contractor(event: MessageCreated, context: BaseContext):
    try:
        c = (text_from_message(event) or "").strip()
        comment = None if not c or c.lower() == "нет" else c
        await context.update_data(owner_comment=comment)
        await answer_message(
            event,
            "Введите дату приезда (в формате ДД.ММ, ДД.ММ.ГГГГ или например '5 июня'):",
        )
        await context.set_state(TemporaryPassStates.INPUT_TRUCK_VISIT_DATE)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, TemporaryPassStates.INPUT_TRUCK_VISIT_DATE)
async def process_truck_visit_date_contractor(event: MessageCreated, context: BaseContext):
    try:
        user_input = (text_from_message(event) or "").strip()
        visit_date = parse_date(user_input)
        now = datetime.datetime.now().date()

        if not visit_date:
            await answer_message(
                event,
                "❌ Неверный формат даты! Введите в формате ДД.ММ, ДД.ММ.ГГГГ или например '5 июня'",
            )
            return

        if visit_date < now:
            await answer_message(event, "Дата не может быть меньше текущей даты. Введите снова:")
            return

        max_date = now + datetime.timedelta(days=31)
        if visit_date > max_date:
            await answer_message(event, "Пропуск нельзя заказать на месяц вперед. Введите снова:")
            return

        data = await context.get_data()
        uid = user_id_from_message(event)
        if uid is None:
            return

        async with AsyncSessionLocal() as session:
            contractor = (
                await session.execute(select(Contractor).where(Contractor.tg_id == uid))
            ).scalar()
            if not contractor:
                await answer_message(event, "❌ Подрядчик не найден")
                await context.clear()
                return

        form = NewTruckPassPaymentForm(
            weight_category=data.get("weight_category") or "",
            car_brand=data.get("car_brand") or "",
            car_number=(data.get("car_number") or "").upper(),
            owner_comment=data.get("owner_comment"),
            visit_date=visit_date,
            days_key="0",
            destination=None,
        )

        created = await create_awaiting_payment_truck_pass(
            owner_type="contractor",
            tg_user_id=uid,
            resident_id=None,
            contractor_id=contractor.id,
            form=form,
        )
        await context.clear()
        if not created:
            await answer_message(
                event,
                "❌ Не удалось создать платёж. Проверьте настройки магазина или попробуйте позже.",
            )
            return
        _pass_id, pay_row_id, conf_url = created
        await send_truck_payment_message(
            user_id=uid,
            form=form,
            confirmation_url=conf_url,
            local_payment_row_id=pay_row_id,
        )
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(TemporaryPassStates.CHOOSE_WEIGHT_CATEGORY, F.callback.payload.startswith("weight_"))
async def process_weight_category(event: MessageCallback, context: BaseContext):
    try:
        weight_category = event.callback.payload.split("_")[-1]
        await context.update_data(weight_category=weight_category)

        # Запрашиваем длину
        keyboard = inline_kb([
            [CallbackButton(text="≤ 7 метров", payload="length_short")],
            [CallbackButton(text="> 7 метров", payload="length_long")]
        ])
        await event.message.answer(text="Выберите длину машины:", attachments=[keyboard.as_markup()])
        await context.set_state(TemporaryPassStates.CHOOSE_LENGTH_CATEGORY)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(TemporaryPassStates.CHOOSE_LENGTH_CATEGORY, F.callback.payload.startswith("length_"))
async def process_length_category(event: MessageCallback, context: BaseContext):
    try:
        length_category = event.callback.payload.split("_")[-1]
        await context.update_data(length_category=length_category)
        await event.message.answer("Укажите тип груза:")
        await context.set_state(TemporaryPassStates.INPUT_CARGO_TYPE)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, TemporaryPassStates.INPUT_CARGO_TYPE)
async def process_cargo_type(event: MessageCreated, context: BaseContext):
    try:
        await context.update_data(cargo_type=(text_from_message(event) or ''))
        await answer_message(event, "Введите номер машины:")
        await context.set_state(TemporaryPassStates.INPUT_CAR_NUMBER)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


# Обработка номера машины
@router.message_created(F.message.body.text, TemporaryPassStates.INPUT_CAR_NUMBER)
async def process_car_number(event: MessageCreated, context: BaseContext):
    try:
        await context.update_data(car_number=(text_from_message(event) or ''))
        await answer_message(event, "Введите марку машины:")
        await context.set_state(TemporaryPassStates.INPUT_CAR_BRAND)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, TemporaryPassStates.INPUT_CAR_BRAND)
async def process_destination(event: MessageCreated, context: BaseContext):
    try:
        await context.update_data(car_brand=(text_from_message(event) or ''))
        await answer_message(event, "Укажите пункт назначения(номер участка):")
        await context.set_state(TemporaryPassStates.INPUT_DESTINATION)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


# Обработка назначения визита
@router.message_created(F.message.body.text, TemporaryPassStates.INPUT_DESTINATION)
async def process_purpose(event: MessageCreated, context: BaseContext):
    try:
        await context.update_data(destination=(text_from_message(event) or ''))
        await context.update_data(purpose='Не указано')
        await answer_message(event, "Введите дату приезда (в формате ДД.ММ, ДД.ММ.ГГГГ или например '5 июня'):")
        await context.set_state(TemporaryPassStates.INPUT_VISIT_DATE)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


# Обработка даты приезда с валидацией
@router.message_created(F.message.body.text, TemporaryPassStates.INPUT_VISIT_DATE)
async def process_visit_date(event: MessageCreated, context: BaseContext):
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

    await context.update_data(visit_date=visit_date)
    keyboard_ = inline_kb([
        [CallbackButton(text="1", payload="days_0")],
        [CallbackButton(text="2", payload="days_1"),
         CallbackButton(text="7", payload="days_6")],
        [CallbackButton(text="14", payload="days_13"),
         CallbackButton(text="30", payload="days_29")]
    ])
    await answer_message(event, "Выберите кол-во дней действия пропуска:", kb=keyboard_)
    await context.set_state(TemporaryPassStates.INPUT_PURPOSE)


@router.message_callback(F.callback.payload.startswith("days_"), TemporaryPassStates.INPUT_PURPOSE)
async def process_days(event: MessageCallback, context: BaseContext):
    try:
        days = int(event.callback.payload.split('_')[1])
        await context.update_data(days=days)
        await event.message.answer("Добавьте комментарий (если не требуется, напишите 'нет'):")
        await context.set_state(TemporaryPassStates.INPUT_COMMENT)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


# Обработка комментария и сохранение данных
@router.message_created(F.message.body.text, TemporaryPassStates.INPUT_COMMENT)
async def process_comment_and_save(event: MessageCreated, context: BaseContext):
    try:
        data = await context.get_data()
        comment = (text_from_message(event) or '') if (text_from_message(event) or '') else None
        status = "pending"  # По умолчанию статус "на рассмотрении"

        async with AsyncSessionLocal() as session:
            # Получаем текущего подрядчика
            contractor = await session.execute(
                select(Contractor).where(Contractor.tg_id == user_id_from_message(event))
            )
            contractor = contractor.scalar()

            if not contractor:
                await answer_message(event, "❌ Ошибка: подрядчик не найден")
                await context.clear()
                return

            # Даты для нового пропуска
            new_visit_date = data['visit_date']
            new_end_date = new_visit_date + datetime.timedelta(days=PASS_TIME)
            keyboard = inline_kb([
                [CallbackButton(text="Оформить временный пропуск", payload="create_temporary_pass")],
                [CallbackButton(text="Назад", payload="back_to_main_menu")]
            ])
            await answer_message(event, "✅ Заявка на временный пропуск отправлена на рассмотрение!", kb=keyboard)
            await context.clear()
            # Проверка лимитов для легковых автомобилей
            count = 0
            if data['vehicle_type'] == 'car':
                # Получаем все подходящие пропуска
                result = await session.execute(
                    select(TemporaryPass).where(
                        TemporaryPass.contractor_id == contractor.id,
                        TemporaryPass.vehicle_type == 'car',
                        TemporaryPass.status == 'approved',
                        TemporaryPass.visit_date <= new_end_date  # Проверка начала существующего <= конца нового
                    )
                )
                for temp_pass in result.scalars().all():
                    days_ = temp_pass.purpose
                    days = 1
                    if days_.isdigit():
                        days = int(days_)
                    old_end_date = temp_pass.visit_date + datetime.timedelta(days=days)
                    if old_end_date >= new_visit_date:
                        count += 1
                if count < MAX_CAR_PASSES:
                    status = "approved"

            # Проверка лимитов для малых грузовых автомобилей
            elif (data['vehicle_type'] == 'truck' and
                  data.get('weight_category') == 'light' and
                  data.get('length_category') == 'short'):
                # Проверяем количество подтвержденных малых грузовых пропусков, пересекающихся по датам
                result = await session.execute(
                    select(TemporaryPass).where(
                        TemporaryPass.contractor_id == contractor.id,
                        TemporaryPass.vehicle_type == 'truck',
                        TemporaryPass.status == 'approved',
                        TemporaryPass.visit_date <= new_end_date  # Проверка начала существующего <= конца нового
                    )
                )
                for temp_pass in result.scalars().all():
                    days_ = temp_pass.purpose
                    days = 1
                    if days_.isdigit():
                        days = int(days_)
                    old_end_date = temp_pass.visit_date + datetime.timedelta(days=days)
                    if old_end_date >= new_visit_date:
                        count += 1
                if count < MAX_TRUCK_PASSES:
                    status = "approved"

        if status == "approved":
            uid_msg = user_id_from_message(event)
            if uid_msg is not None:
                asyncio.create_task(
                    _notify_contractor_temp_pass_auto_approved_delayed(
                        uid_msg,
                        random.randint(180, 720),
                        (data.get("car_number") or "").upper(),
                        contractor.company or "",
                        contractor.position or "",
                        contractor.fio or "",
                    )
                )
        else:
            tg_ids = await get_active_admins_and_managers_tg_ids()
            for tg_id in tg_ids:
                try:
                    await bot.send_message(
                        user_id=tg_id,
                        text=(
                            "Поступила заявка на временный пропуск от подрядчика "
                            f"{fio_html(contractor.fio, contractor.tg_id)}.\n"
                            "(Пропуска > Временные пропуска > На утверждении)"
                        ),
                        parse_mode=ParseMode.HTML,
                        attachments=[main_menu_inline_button_kb().as_markup()],
                    )
                    await asyncio.sleep(0.05)
                except:
                    pass
        new_pass = TemporaryPass(
            owner_type="contractor",
            contractor_id=contractor.id,
            vehicle_type=data.get("vehicle_type"),
            weight_category=data.get("weight_category", None),
            length_category=data.get("length_category", None),
            car_number=data.get("car_number").upper(),
            car_brand=data.get("car_brand"),
            cargo_type=data.get("cargo_type"),
            purpose=str(data.get("days")),
            destination=data.get("destination"),
            visit_date=new_visit_date,
            owner_comment=comment,
            status=status,
            created_at=datetime.datetime.now(),
            time_registration=datetime.datetime.now() if status == "approved" else None
        )

        async with AsyncSessionLocal() as session:
            session.add(new_pass)
            await session.commit()
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


# Обработчики разделов временных пропусков
@router.message_callback(F.callback.payload == "my_pending_temp_passes")
async def show_my_pending_temp_passes(event: MessageCallback, context: BaseContext):
    try:
        await context.set_state(TemporaryPassViewStates.VIEWING_PENDING)
        await context.update_data(temp_pass_page=0, temp_pass_status='pending')
        await show_my_temp_passes(event, context)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "my_approved_temp_passes")
async def show_my_approved_temp_passes(event: MessageCallback, context: BaseContext):
    try:
        await context.set_state(TemporaryPassViewStates.VIEWING_APPROVED)
        await context.update_data(temp_pass_page=0, temp_pass_status='approved')
        await show_my_temp_passes(event, context)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "my_rejected_temp_passes")
async def show_my_rejected_temp_passes(event: MessageCallback, context: BaseContext):
    try:
        await context.set_state(TemporaryPassViewStates.VIEWING_REJECTED)
        await context.update_data(temp_pass_page=0, temp_pass_status='rejected')
        await show_my_temp_passes(event, context)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


# Функция отображения списка временных пропусков
async def show_my_temp_passes(message: MessageCreated | MessageCallback, context: BaseContext):
    try:
        data = await context.get_data()
        page = data.get('temp_pass_page', 0)
        status = data.get('temp_pass_status', 'pending')

        async with AsyncSessionLocal() as session:
            contractor = await session.execute(
                select(Contractor).where(Contractor.tg_id == _uid(message))
            )
            contractor = contractor.scalar()

            if not contractor:
                if isinstance(message, MessageCallback):
                    await send_user(bot, message.callback.user.user_id, "❌ Подрядчик не найден")
                    await callback_ack(bot, message)
                else:
                    await answer_message(message, "❌ Подрядчик не найден")
                return

            if status == "pending":
                st_cond = or_(
                    TemporaryPass.status == "pending",
                    TemporaryPass.status == "awaiting_payment",
                )
            else:
                st_cond = TemporaryPass.status == status

            # Получаем общее количество пропусков
            total_count = await session.scalar(
                select(func.count(TemporaryPass.id))
                .where(
                    TemporaryPass.contractor_id == contractor.id,
                    st_cond,
                )
            )

            # Получаем пропуска для текущей страницы
            result = await session.execute(
                select(TemporaryPass)
                .where(
                    TemporaryPass.contractor_id == contractor.id,
                    st_cond,
                )
                .order_by(TemporaryPass.created_at.desc())
                .offset(page * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )
            passes = result.scalars().all()

        if not passes:
            if isinstance(message, MessageCallback):
                await callback_ack(bot, message)
                return
            await answer_message(message, "У вас нет временных пропусков в этом разделе")
            return

        # Формируем кнопки
        buttons = []
        for pass_item in passes:
            # Формируем текст кнопки: дата + номер машины
            btn_text = f"{pass_item.visit_date.strftime('%d.%m.%Y')} - {pass_item.car_number}"
            if len(btn_text) > 30:
                btn_text = btn_text[:27] + "..."
            buttons.append(
                [CallbackButton(
                    text=btn_text,
                    payload=f"view_my_temp_pass_{pass_item.id}"
                )]
            )

        # Кнопки пагинации
        pagination_buttons = []
        if page > 0:
            pagination_buttons.append(
                CallbackButton(text="⬅️ Предыдущие", payload="my_temp_pass_prev")
            )

        if (page + 1) * PAGE_SIZE < total_count:
            pagination_buttons.append(
                CallbackButton(text="Следующие ➡️", payload="my_temp_pass_next")
            )

        if pagination_buttons:
            buttons.append(pagination_buttons)

        buttons.append(
            [CallbackButton(text="⬅️ Назад", payload="temporary_pass_menu")]
        )

        status_text = {
            'pending': "на подтверждении",
            'approved': "подтвержденные",
            'rejected': "отклоненные"
        }.get(status, "")

        text = f"Ваши временные пропуска ({status_text}):"
        if isinstance(message, MessageCallback):
            await edit_or_send_callback(bot, message, text, inline_kb(buttons))
        else:
            await answer_message(message, text, kb=inline_kb(buttons))
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{_uid(message)} - {str(e)}')
        await asyncio.sleep(0.05)


# Обработчики пагинации для временных пропусков
@router.message_callback(F.callback.payload == "my_temp_pass_prev", *states_in_group(TemporaryPassViewStates))
async def handle_my_temp_pass_prev(event: MessageCallback, context: BaseContext):
    try:
        data = await context.get_data()
        current_page = data.get('temp_pass_page', 0)
        if current_page > 0:
            await context.update_data(temp_pass_page=current_page - 1)
            await show_my_temp_passes(event, context)
        await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "my_temp_pass_next", *states_in_group(TemporaryPassViewStates))
async def handle_my_temp_pass_next(event: MessageCallback, context: BaseContext):
    try:
        data = await context.get_data()
        current_page = data.get('temp_pass_page', 0)
        await context.update_data(temp_pass_page=current_page + 1)
        await show_my_temp_passes(event, context)
        await callback_ack(bot, event)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


# Просмотр деталей временного пропуска
@router.message_callback(F.callback.payload.startswith("view_my_temp_pass_"))
async def view_my_temp_pass_details(event: MessageCallback):
    try:
        pass_id = int(event.callback.payload.split("_")[-1])

        async with AsyncSessionLocal() as session:
            pass_item = await session.get(TemporaryPass, pass_id)
            if not pass_item:
                await callback_ack(bot, event, "Пропуск не найден")
                return

            # Формируем текст
            status_text = {
                'pending': "⏳ На рассмотрении",
                'awaiting_payment': "💳 Ожидание оплаты",
                'approved': "✅ Подтвержден",
                'rejected': "❌ Отклонен"
            }.get(pass_item.status, "")

            if is_new_truck_pass(pass_item):
                text = (
                    f"Статус: {status_text}\n"
                    f"Тип ТС: Грузовая\n"
                    f"Категория: {pass_item.weight_category or ''}\n"
                    f"Марка: {pass_item.car_brand}\n"
                    f"Номер: {pass_item.car_number}\n"
                    f"Дата визита: {pass_item.visit_date.strftime('%d.%m.%Y')}\n"
                    f"Длительность: {temp_pass_duration_label(pass_item.purpose).strip()}\n"
                    f"Комментарий владельца: {pass_item.owner_comment or 'нет'}"
                )
            else:
                vehicle_type = "Легковая" if pass_item.vehicle_type == "car" else "Грузовая"
                weight_category = ""
                length_category = ""
                cargo_type = ""

                if pass_item.vehicle_type == "truck":
                    weight_category = "\nТоннаж: " + ("≤ 12 тонн" if pass_item.weight_category == "light" else "> 12 тонн")
                    length_category = "\nДлина: " + ("≤ 7 метров" if pass_item.length_category == "short" else "> 7 метров")
                    cargo_type = f"\n{pass_item.cargo_type}"
                if pass_item.purpose in ['6', '13', '29']:
                    value = f'{int(pass_item.purpose) + 1} дней\n'
                elif pass_item.purpose == '1':
                    value = '2 дня\n'
                else:
                    value = '1 день\n'
                text = (
                    f"Статус: {status_text}\n"
                    f"Тип ТС: {vehicle_type}"
                    f"{weight_category}"
                    f"{length_category}"
                    f"{cargo_type}\n"
                    f"Номер: {pass_item.car_number}\n"
                    f"Марка: {pass_item.car_brand}\n"
                    f"Пункт назначения: {pass_item.destination}\n"
                    f"Дата визита: {pass_item.visit_date.strftime('%d.%m.%Y')}\n"
                    f"Действие пропуска: {value}"
                    f"Комментарий: {pass_item.owner_comment or 'нет'}"
                )

            if pass_item.status == 'rejected' and pass_item.resident_comment:
                text += f"\n\nПричина отклонения:\n{pass_item.resident_comment}"

            keyboard = inline_kb([
                [CallbackButton(text="⬅️ Назад", payload="back_to_my_temp_passes")]
            ])

            await edit_or_send_callback(bot, event, text, keyboard)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


# Возврат к списку временных пропусков
@router.message_callback(F.callback.payload == "back_to_my_temp_passes")
async def back_to_my_temp_passes(event: MessageCallback, context: BaseContext):
    try:
        await show_my_temp_passes(event, context)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_callback(F.callback.payload == "register_contractor")
async def start_contractor_registration(event: MessageCallback, context: BaseContext):
    try:
        await context.set_state(ContractorContractorRegistration.INPUT_PHONE)
        await event.message.answer("Введите телефон подрядчика:")
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{event.callback.user.user_id} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, ContractorContractorRegistration.INPUT_PHONE)
async def process_contractor_phone(event: MessageCreated, context: BaseContext):
    try:
        phone = (text_from_message(event) or '')
        if not is_valid_phone(phone):
            await answer_message(event, 'Телефон должен быть в формате 8XXXXXXXXXX.\nПопробуйте ввести еще раз!')
            return
        await context.update_data(phone=(text_from_message(event) or ''))
        await answer_message(event, "Укажите виды выполняемых работ:")
        await context.set_state(ContractorContractorRegistration.INPUT_WORK_TYPES)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)


@router.message_created(F.message.body.text, ContractorContractorRegistration.INPUT_WORK_TYPES)
async def process_work_types(event: MessageCreated, context: BaseContext):
    try:
        data = await context.get_data()

        async with AsyncSessionLocal() as session:
            contractor = await session.execute(
                select(Contractor).where(Contractor.tg_id == user_id_from_message(event)))
            contractor = contractor.scalar()

            new_request = ContractorContractorRequest(
                contractor_id=contractor.id,
                phone=data['phone'],
                work_types=(text_from_message(event) or '')
            )
            session.add(new_request)
            await session.commit()

            await answer_message(event, "✅ Заявка на регистрацию субподрядчика отправлена администратору!")
            tg_ids = await get_active_admins_and_managers_tg_ids()
            for tg_id in tg_ids:
                try:
                    await send_user(
                        bot,
                        tg_id,
                        text=(
                            "Поступила заявка на регистрацию субподрядчика от подрядчика "
                            f"{fio_html(contractor.fio, contractor.tg_id)}, "
                            f"{html_lib.escape(str(contractor.company or ''))}, "
                            f"{html_lib.escape(str(contractor.position or ''))}.\n"
                            "(Регистрация > Заявки субподрядчиков от подрядчиков)"
                        ),
                        kb=main_menu_inline_button_kb(),
                        parse_mode=ParseMode.HTML,
                        main_menu_attachment=True,
                    )
                except:
                    pass
            text = (
                f"ФИО: {fio_html(contractor.fio, contractor.tg_id)}\n"
                f"{profile_link_line_html(contractor.first_name, contractor.last_name, contractor.tg_id, fallback_fio=contractor.fio)}"
                f"Компания: {html_lib.escape(str(contractor.company or ''))}\n"
                f"Должность: {html_lib.escape(str(contractor.position or ''))}\n"
            )
            menu_kb = contractor_main_menu_kb(bool(contractor.can_add_contractor))

            await answer_message(event, text=text, kb=menu_kb, parse_mode=ParseMode.HTML)
            await context.clear()
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f'{user_id_from_message(event)} - {str(e)}')
        await asyncio.sleep(0.05)
        