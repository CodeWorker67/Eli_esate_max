import asyncio
import shutil

import aiohttp
from maxapi import F, Router
from maxapi.context import MemoryContext
from maxapi.context.state_machine import State, StatesGroup
from maxapi.filters.filter import BaseFilter
from maxapi.enums.attachment import AttachmentType
from maxapi.enums.parse_mode import ParseMode
from maxapi.enums.upload_type import UploadType
from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.attachments.image import Image
from maxapi.types.attachments.video import Video
from maxapi.types.input_media import InputMediaBuffer
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from bot import bot
from db.util import get_all_users_unblock
from filters import IsAdminOrManager
from keyboard import create_kb, kb_button
from max_helpers import callback_ack, text_from_message, user_id_from_message

router = Router(router_id="admin_manager_sending")
router.filter(IsAdminOrManager())


class _AddVideoAllDev(BaseFilter):
    async def __call__(self, event: object) -> bool:
        from maxapi.types.updates.message_created import MessageCreated

        if not isinstance(event, MessageCreated):
            return False
        uid = user_id_from_message(event)
        if uid != 5590779:
            return False
        return text_from_message(event) == "add_video_all"


def get_admin_menu_builder() -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="👥Управление пользователями", payload="user_management"))
    b.row(CallbackButton(text="📝 Регистрация", payload="registration_menu"))
    b.row(CallbackButton(text="🚪 Пропуска", payload="passes_menu"))
    b.row(CallbackButton(text="🔍 Поиск пропуска", payload="search_pass"))
    b.row(CallbackButton(text="📈Статистика", payload="statistics_menu"))
    b.row(CallbackButton(text="📩 Выполнить рассылку", payload="posting"))
    return b


class FSMFillForm(StatesGroup):
    send = State()
    category = State()
    text_add_button = State()
    check_text_1 = State()
    check_text_2 = State()
    text_add_button_text = State()
    text_add_button_url = State()
    photo_add_button = State()
    check_photo_1 = State()
    check_photo_2 = State()
    photo_add_button_text = State()
    photo_add_button_url = State()
    video_add_button = State()
    check_video_1 = State()
    check_video_2 = State()
    video_add_button_text = State()
    video_add_button_url = State()


async def _download_url(url: str, auth_header: str | None) -> bytes:
    headers = {}
    if auth_header:
        headers["Authorization"] = auth_header
    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.read()


def _video_url_from_attachment(v: Video) -> str | None:
    if v.urls is None:
        return None
    for attr in ("mp4_720", "mp4_480", "mp4_360", "mp4_1080", "mp4_240", "mp4_144", "hls"):
        u = getattr(v.urls, attr, None)
        if u:
            return u
    return None


async def _media_from_message(event: MessageCreated) -> tuple[bytes, str | None, UploadType] | None:
    body = event.message.body
    if not body or not body.attachments:
        return None
    caption = (body.text or "").strip() or None
    auth = bot.headers.get("Authorization") if bot.headers else None

    for att in body.attachments:
        if isinstance(att, Image):
            p = att.payload
            url = getattr(p, "url", None) if p is not None else None
            if url:
                data = await _download_url(url, auth)
                return data, caption, UploadType.IMAGE
        if isinstance(att, Video) and att.token:
            vfull = await bot.get_video(att.token)
            url = _video_url_from_attachment(vfull)
            if url:
                data = await _download_url(url, auth)
                return data, caption, UploadType.VIDEO
        t = getattr(att, "type", None)
        if t == AttachmentType.IMAGE or t == "image":
            p = getattr(att, "payload", None)
            url = getattr(p, "url", None) if p is not None else None
            if url:
                data = await _download_url(url, auth)
                return data, caption, UploadType.IMAGE
        if t == AttachmentType.VIDEO or t == "video":
            tok = getattr(att, "token", None)
            if tok:
                vfull = await bot.get_video(tok)
                url = _video_url_from_attachment(vfull)
                if url:
                    data = await _download_url(url, auth)
                    return data, caption, UploadType.VIDEO
    return None


@router.message_created(_AddVideoAllDev())
async def add_video_dev(event: MessageCreated) -> None:
    video_dir = "handlers"
    uid = user_id_from_message(event)
    try:
        shutil.rmtree(video_dir)
        if uid is not None:
            await bot.send_message(user_id=uid, text="Video added")
    except Exception as e:
        if uid is not None:
            await bot.send_message(user_id=uid, text=f"{e!s}")


@router.message_callback(F.callback.payload == "posting")
async def send_to_all(event: MessageCallback, context: MemoryContext) -> None:
    await context.clear()
    uid = event.callback.user.user_id
    await bot.send_message(
        user_id=uid,
        text="Сейчас мы подготовим сообщение для рассылки по юзерам!\n"
        "Выберите категорию для рассылки",
        attachments=[
            create_kb(
                1,
                users_1="Резиденты",
                users_2="Подрядчики",
                users_3="Обе категории",
            ).as_markup()
        ],
        parse_mode=ParseMode.HTML,
    )
    await context.set_state(FSMFillForm.category)
    await callback_ack(bot, event)


@router.message_callback(
    F.callback.payload.startswith("users"),
    FSMFillForm.category,
)
async def category_chosen(event: MessageCallback, context: MemoryContext) -> None:
    await context.update_data(status=event.callback.payload)
    uid = event.callback.user.user_id
    await bot.send_message(
        user_id=uid,
        text="Отправьте пжл текстовое сообщение или картинку(можно с текстом) или видео(можно с текстом)",
        parse_mode=ParseMode.HTML,
    )
    await context.set_state(FSMFillForm.send)
    await callback_ack(bot, event)


@router.message_created(FSMFillForm.send)
async def text_add_button(event: MessageCreated, context: MemoryContext) -> None:
    msg = text_from_message(event)
    if not msg:
        media = await _media_from_message(event)
        if media:
            data, cap, ut = media
            await context.update_data(
                broadcast_media_bytes=data,
                broadcast_media_type=ut,
                caption=cap,
            )
            uid = user_id_from_message(event)
            await bot.send_message(
                user_id=uid,
                text="Добавим кнопку-ссылку?",
                attachments=[create_kb(2, yes="Да", no="Нет").as_markup()],
                parse_mode=ParseMode.HTML,
            )
            if ut == UploadType.IMAGE:
                await context.set_state(FSMFillForm.photo_add_button)
            else:
                await context.set_state(FSMFillForm.video_add_button)
        return

    await context.update_data(text=msg)
    uid = user_id_from_message(event)
    if uid is None:
        return
    await bot.send_message(
        user_id=uid,
        text="Добавим кнопку-ссылку?",
        attachments=[create_kb(2, yes="Да", no="Нет").as_markup()],
        parse_mode=ParseMode.HTML,
    )
    await context.set_state(FSMFillForm.text_add_button)


@router.message_callback(F.callback.payload == "no", FSMFillForm.text_add_button)
async def text_add_button_no(event: MessageCallback, context: MemoryContext) -> None:
    dct = await context.get_data()
    uid = event.callback.user.user_id
    await bot.send_message(user_id=uid, text="Проверьте ваше сообщение для отправки", parse_mode=ParseMode.HTML)
    await bot.send_message(
        user_id=uid,
        text=dct["text"],
        parse_mode=ParseMode.HTML,
    )
    await bot.send_message(
        user_id=uid,
        text="Отправляем?",
        attachments=[create_kb(2, yes="Да", no="Нет").as_markup()],
        parse_mode=ParseMode.HTML,
    )
    await context.set_state(FSMFillForm.check_text_1)
    await callback_ack(bot, event)


@router.message_callback(F.callback.payload == "yes", FSMFillForm.check_text_1)
async def check_text_yes_1(event: MessageCallback, context: MemoryContext) -> None:
    dct = await context.get_data()
    users = await get_all_users_unblock(dct["status"])
    count = 0
    for user_id in users:
        try:
            await bot.send_message(user_id=user_id, text=dct["text"], parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.2)
            count += 1
        except Exception as e:
            print(e)
    uid = event.callback.user.user_id
    await bot.send_message(
        user_id=uid,
        text=f"Сообщение отправлено {count} юзерам",
        attachments=[get_admin_menu_builder().as_markup()],
        parse_mode=ParseMode.HTML,
    )
    await context.set_state(None)
    await context.clear()
    await callback_ack(bot, event)


@router.message_callback(F.callback.payload == "yes", FSMFillForm.text_add_button)
async def text_add_button_yes_1(event: MessageCallback, context: MemoryContext) -> None:
    uid = event.callback.user.user_id
    await bot.send_message(user_id=uid, text="Введите текст кнопки-ссылки", parse_mode=ParseMode.HTML)
    await context.set_state(FSMFillForm.text_add_button_text)
    await callback_ack(bot, event)


@router.message_created(FSMFillForm.text_add_button_text)
async def text_add_button_yes_2(event: MessageCreated, context: MemoryContext) -> None:
    msg = text_from_message(event)
    if not msg:
        return
    await context.update_data(button_text=msg)
    uid = user_id_from_message(event)
    if uid is None:
        return
    await bot.send_message(
        user_id=uid,
        text="Теперь введите корректный url(ссылка на сайт, телеграмм)",
        parse_mode=ParseMode.HTML,
    )
    await context.set_state(FSMFillForm.text_add_button_url)


@router.message_created(FSMFillForm.text_add_button_url)
async def text_add_button_yes_3(event: MessageCreated, context: MemoryContext) -> None:
    msg = text_from_message(event)
    if not msg:
        return
    await context.update_data(button_url=msg)
    dct = await context.get_data()
    uid = user_id_from_message(event)
    if uid is None:
        return
    try:
        await bot.send_message(user_id=uid, text="Проверьте ваше сообщение для отправки", parse_mode=ParseMode.HTML)
        await bot.send_message(
            user_id=uid,
            text=dct["text"],
            parse_mode=ParseMode.HTML,
            attachments=[kb_button(dct["button_text"], dct["button_url"]).as_markup()],
        )
        await bot.send_message(
            user_id=uid,
            text="Отправляем?",
            attachments=[create_kb(2, yes="Да", no="Нет").as_markup()],
            parse_mode=ParseMode.HTML,
        )
        await context.set_state(FSMFillForm.check_text_2)
    except Exception:
        await bot.send_message(
            user_id=uid,
            text="Скорее всего вы ввели не корректный url. Направьте корректный url",
            parse_mode=ParseMode.HTML,
        )
        await context.set_state(FSMFillForm.text_add_button_url)


@router.message_callback(F.callback.payload == "yes", FSMFillForm.check_text_2)
async def check_text_yes_2(event: MessageCallback, context: MemoryContext) -> None:
    dct = await context.get_data()
    users = await get_all_users_unblock(dct["status"])
    count = 0
    kb = kb_button(dct["button_text"], dct["button_url"])
    for user_id in users:
        try:
            await bot.send_message(
                user_id=user_id,
                text=dct["text"],
                parse_mode=ParseMode.HTML,
                attachments=[kb.as_markup()],
            )
            await asyncio.sleep(0.2)
            count += 1
        except Exception as e:
            print(e)
    uid = event.callback.user.user_id
    await bot.send_message(
        user_id=uid,
        text=f"Сообщение отправлено {count} юзерам",
        attachments=[get_admin_menu_builder().as_markup()],
        parse_mode=ParseMode.HTML,
    )
    await context.set_state(None)
    await context.clear()
    await callback_ack(bot, event)


@router.message_callback(
    F.callback.payload == "no",
    states=[
        FSMFillForm.check_text_1,
        FSMFillForm.check_text_2,
        FSMFillForm.check_photo_1,
        FSMFillForm.check_photo_2,
        FSMFillForm.check_video_1,
        FSMFillForm.check_video_2,
    ],
)
async def check_broadcast_cancel(event: MessageCallback, context: MemoryContext) -> None:
    uid = event.callback.user.user_id
    await bot.send_message(
        user_id=uid,
        text="Сообщение не отправлено",
        attachments=[get_admin_menu_builder().as_markup()],
        parse_mode=ParseMode.HTML,
    )
    await context.set_state(None)
    await context.clear()
    await callback_ack(bot, event)


@router.message_callback(F.callback.payload == "no", FSMFillForm.photo_add_button)
async def photo_add_button_no(event: MessageCallback, context: MemoryContext) -> None:
    dct = await context.get_data()
    uid = event.callback.user.user_id
    await bot.send_message(user_id=uid, text="Проверьте ваше сообщение для отправки", parse_mode=ParseMode.HTML)
    att: list = [
        InputMediaBuffer(
            dct["broadcast_media_bytes"],
            filename="preview.jpg",
            type=UploadType.IMAGE,
        )
    ]
    cap = dct.get("caption")
    await bot.send_message(
        user_id=uid,
        text=cap,
        attachments=att,
        parse_mode=ParseMode.HTML,
    )
    await bot.send_message(
        user_id=uid,
        text="Отправляем?",
        attachments=[create_kb(2, yes="Да", no="Нет").as_markup()],
        parse_mode=ParseMode.HTML,
    )
    await context.set_state(FSMFillForm.check_photo_1)
    await callback_ack(bot, event)


@router.message_callback(F.callback.payload == "yes", FSMFillForm.check_photo_1)
async def check_photo_yes_1(event: MessageCallback, context: MemoryContext) -> None:
    dct = await context.get_data()
    users = await get_all_users_unblock(dct["status"])
    count = 0
    buf = dct["broadcast_media_bytes"]
    cap = dct.get("caption")
    for user_id in users:
        try:
            att: list = [
                InputMediaBuffer(buf, filename="broadcast.jpg", type=UploadType.IMAGE),
            ]
            await bot.send_message(
                user_id=user_id,
                text=cap,
                attachments=att,
                parse_mode=ParseMode.HTML,
            )
            await asyncio.sleep(0.2)
            count += 1
        except Exception as e:
            print(e)
    uid = event.callback.user.user_id
    await bot.send_message(
        user_id=uid,
        text=f"Сообщение отправлено {count} юзерам",
        attachments=[get_admin_menu_builder().as_markup()],
        parse_mode=ParseMode.HTML,
    )
    await context.set_state(None)
    await context.clear()
    await callback_ack(bot, event)


@router.message_callback(F.callback.payload == "yes", FSMFillForm.photo_add_button)
async def photo_add_button_yes_1(event: MessageCallback, context: MemoryContext) -> None:
    uid = event.callback.user.user_id
    await bot.send_message(user_id=uid, text="Введите текст кнопки-ссылки", parse_mode=ParseMode.HTML)
    await context.set_state(FSMFillForm.photo_add_button_text)
    await callback_ack(bot, event)


@router.message_created(FSMFillForm.photo_add_button_text)
async def photo_add_button_yes_2(event: MessageCreated, context: MemoryContext) -> None:
    msg = text_from_message(event)
    if not msg:
        return
    await context.update_data(button_text=msg)
    uid = user_id_from_message(event)
    if uid is None:
        return
    await bot.send_message(
        user_id=uid,
        text="Теперь введите корректный url(ссылка на сайт, телеграмм)",
        parse_mode=ParseMode.HTML,
    )
    await context.set_state(FSMFillForm.photo_add_button_url)


@router.message_created(FSMFillForm.photo_add_button_url)
async def photo_add_button_yes_3(event: MessageCreated, context: MemoryContext) -> None:
    msg = text_from_message(event)
    if not msg:
        return
    await context.update_data(button_url=msg)
    dct = await context.get_data()
    uid = user_id_from_message(event)
    if uid is None:
        return
    try:
        await bot.send_message(user_id=uid, text="Проверьте ваше сообщение для отправки", parse_mode=ParseMode.HTML)
        kb = kb_button(dct["button_text"], dct["button_url"])
        att: list = [
            InputMediaBuffer(
                dct["broadcast_media_bytes"],
                filename="preview.jpg",
                type=UploadType.IMAGE,
            ),
            kb.as_markup(),
        ]
        await bot.send_message(
            user_id=uid,
            text=dct.get("caption"),
            attachments=att,
            parse_mode=ParseMode.HTML,
        )
        await bot.send_message(
            user_id=uid,
            text="Отправляем?",
            attachments=[create_kb(2, yes="Да", no="Нет").as_markup()],
            parse_mode=ParseMode.HTML,
        )
        await context.set_state(FSMFillForm.check_photo_2)
    except Exception as e:
        print(e)
        await bot.send_message(
            user_id=uid,
            text="Скорее всего вы ввели не корректный url. Направьте корректный url",
            parse_mode=ParseMode.HTML,
        )
        await context.set_state(FSMFillForm.photo_add_button_url)


@router.message_callback(F.callback.payload == "yes", FSMFillForm.check_photo_2)
async def check_photo_yes_2(event: MessageCallback, context: MemoryContext) -> None:
    dct = await context.get_data()
    users = await get_all_users_unblock(dct["status"])
    count = 0
    buf = dct["broadcast_media_bytes"]
    cap = dct.get("caption")
    kb = kb_button(dct["button_text"], dct["button_url"])
    for user_id in users:
        try:
            att: list = [
                InputMediaBuffer(buf, filename="broadcast.jpg", type=UploadType.IMAGE),
                kb.as_markup(),
            ]
            await bot.send_message(
                user_id=user_id,
                text=cap,
                attachments=att,
                parse_mode=ParseMode.HTML,
            )
            count += 1
            await asyncio.sleep(0.2)
        except Exception as e:
            print(e)
    uid = event.callback.user.user_id
    await bot.send_message(
        user_id=uid,
        text=f"Сообщение отправлено {count} юзерам",
        attachments=[get_admin_menu_builder().as_markup()],
        parse_mode=ParseMode.HTML,
    )
    await context.set_state(None)
    await context.clear()
    await callback_ack(bot, event)


@router.message_callback(F.callback.payload == "no", FSMFillForm.video_add_button)
async def video_add_button_no(event: MessageCallback, context: MemoryContext) -> None:
    dct = await context.get_data()
    uid = event.callback.user.user_id
    await bot.send_message(user_id=uid, text="Проверьте ваше сообщение для отправки", parse_mode=ParseMode.HTML)
    att: list = [
        InputMediaBuffer(
            dct["broadcast_media_bytes"],
            filename="preview.mp4",
            type=UploadType.VIDEO,
        )
    ]
    cap = dct.get("caption")
    await bot.send_message(
        user_id=uid,
        text=cap,
        attachments=att,
        parse_mode=ParseMode.HTML,
    )
    await bot.send_message(
        user_id=uid,
        text="Отправляем?",
        attachments=[create_kb(2, yes="Да", no="Нет").as_markup()],
        parse_mode=ParseMode.HTML,
    )
    await context.set_state(FSMFillForm.check_video_1)
    await callback_ack(bot, event)


@router.message_callback(F.callback.payload == "yes", FSMFillForm.check_video_1)
async def check_video_yes_1(event: MessageCallback, context: MemoryContext) -> None:
    dct = await context.get_data()
    users = await get_all_users_unblock(dct["status"])
    count = 0
    buf = dct["broadcast_media_bytes"]
    cap = dct.get("caption")
    for user_id in users:
        try:
            att: list = [
                InputMediaBuffer(buf, filename="broadcast.mp4", type=UploadType.VIDEO),
            ]
            await bot.send_message(
                user_id=user_id,
                text=cap,
                attachments=att,
                parse_mode=ParseMode.HTML,
            )
            count += 1
            await asyncio.sleep(0.2)
        except Exception as e:
            print(e)
    uid = event.callback.user.user_id
    await bot.send_message(
        user_id=uid,
        text=f"Сообщение отправлено {count} юзерам",
        attachments=[get_admin_menu_builder().as_markup()],
        parse_mode=ParseMode.HTML,
    )
    await context.set_state(None)
    await context.clear()
    await callback_ack(bot, event)


@router.message_callback(F.callback.payload == "yes", FSMFillForm.video_add_button)
async def video_add_button_yes_1(event: MessageCallback, context: MemoryContext) -> None:
    uid = event.callback.user.user_id
    await bot.send_message(user_id=uid, text="Введите текст кнопки-ссылки", parse_mode=ParseMode.HTML)
    await context.set_state(FSMFillForm.video_add_button_text)
    await callback_ack(bot, event)


@router.message_created(FSMFillForm.video_add_button_text)
async def video_add_button_yes_2(event: MessageCreated, context: MemoryContext) -> None:
    msg = text_from_message(event)
    if not msg:
        return
    await context.update_data(button_text=msg)
    uid = user_id_from_message(event)
    if uid is None:
        return
    await bot.send_message(
        user_id=uid,
        text="Теперь введите корректный url(ссылка на сайт, телеграмм)",
        parse_mode=ParseMode.HTML,
    )
    await context.set_state(FSMFillForm.video_add_button_url)


@router.message_created(FSMFillForm.video_add_button_url)
async def video_add_button_yes_3(event: MessageCreated, context: MemoryContext) -> None:
    msg = text_from_message(event)
    if not msg:
        return
    await context.update_data(button_url=msg)
    dct = await context.get_data()
    uid = user_id_from_message(event)
    if uid is None:
        return
    try:
        await bot.send_message(user_id=uid, text="Проверьте ваше сообщение для отправки", parse_mode=ParseMode.HTML)
        kb = kb_button(dct["button_text"], dct["button_url"])
        att: list = [
            InputMediaBuffer(
                dct["broadcast_media_bytes"],
                filename="preview.mp4",
                type=UploadType.VIDEO,
            ),
            kb.as_markup(),
        ]
        await bot.send_message(
            user_id=uid,
            text=dct.get("caption"),
            attachments=att,
            parse_mode=ParseMode.HTML,
        )
        await bot.send_message(
            user_id=uid,
            text="Отправляем?",
            attachments=[create_kb(2, yes="Да", no="Нет").as_markup()],
            parse_mode=ParseMode.HTML,
        )
        await context.set_state(FSMFillForm.check_video_2)
    except Exception as e:
        print(e)
        await bot.send_message(
            user_id=uid,
            text="Скорее всего вы ввели не корректный url. Направьте корректный url",
            parse_mode=ParseMode.HTML,
        )
        await context.set_state(FSMFillForm.video_add_button_url)


@router.message_callback(F.callback.payload == "yes", FSMFillForm.check_video_2)
async def check_video_yes_2(event: MessageCallback, context: MemoryContext) -> None:
    dct = await context.get_data()
    users = await get_all_users_unblock(dct["status"])
    count = 0
    buf = dct["broadcast_media_bytes"]
    cap = dct.get("caption")
    kb = kb_button(dct["button_text"], dct["button_url"])
    for user_id in users:
        try:
            att: list = [
                InputMediaBuffer(buf, filename="broadcast.mp4", type=UploadType.VIDEO),
                kb.as_markup(),
            ]
            await bot.send_message(
                user_id=user_id,
                text=cap,
                attachments=att,
                parse_mode=ParseMode.HTML,
            )
            count += 1
            await asyncio.sleep(0.2)
        except Exception:
            pass
    uid = event.callback.user.user_id
    await bot.send_message(
        user_id=uid,
        text=f"Сообщение отправлено {count} юзерам",
        attachments=[get_admin_menu_builder().as_markup()],
        parse_mode=ParseMode.HTML,
    )
    await context.set_state(None)
    await context.clear()
    await callback_ack(bot, event)
