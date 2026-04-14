from __future__ import annotations

import html as html_std
from typing import TYPE_CHECKING

from maxapi.context.state_machine import State
from maxapi.enums.parse_mode import ParseMode
from maxapi.utils.formatting import UserMention
from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

if TYPE_CHECKING:
    from maxapi.bot import Bot


def text_from_message(event: MessageCreated) -> str | None:
    if not event.message.body:
        return None
    t = event.message.body.text
    if t is None:
        return None
    s = t.strip()
    return s if s else None


def user_id_from_message(event: MessageCreated) -> int | None:
    s = event.message.sender
    return s.user_id if s else None


def inline_kb(rows: list[list[CallbackButton]]) -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    for row in rows:
        b.row(*row)
    return b


def main_menu_inline_button_kb() -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="Главное меню", payload="main_menu_inline"))
    return b


def fio_html(fio: str | None, max_user_id: int | None) -> str:
    """ФИО как HTML: ссылка на профиль max://user/{id} при известном id пользователя MAX.

    Без id (обнулённые данные) — обычный текст; при пустом ФИО — «Нет данных».
    """
    if max_user_id is None:
        label = (fio or "").strip() or "Нет данных"
        return html_std.escape(label)
    label = (fio or "").strip() or "Не указано"
    return UserMention(label, user_id=int(max_user_id)).as_html()


def max_profile_display_label(
    first_name: str | None,
    last_name: str | None,
    *,
    fallback_fio: str | None = None,
) -> str:
    label = f"{first_name or ''} {(last_name or '').strip()}".strip()
    if label:
        return label
    fb = (fallback_fio or "").strip()
    return fb if fb else "Не указано"


def profile_link_line_html(
    first_name: str | None,
    last_name: str | None,
    max_user_id: int | None,
    *,
    fallback_fio: str | None = None,
) -> str:
    if max_user_id is None:
        return "Ссылка на профиль: Нет данных\n"
    disp = max_profile_display_label(
        first_name, last_name, fallback_fio=fallback_fio
    )
    return f"Ссылка на профиль: {fio_html(disp, max_user_id)}\n"


def states_in_group(group_cls: type) -> list[State]:
    return [
        getattr(group_cls, name)
        for name in dir(group_cls)
        if isinstance(getattr(group_cls, name, None), State)
    ]


async def answer_message(
    event: MessageCreated,
    text: str,
    kb: InlineKeyboardBuilder | None = None,
    parse_mode: ParseMode = ParseMode.HTML,
) -> None:
    att = [kb.as_markup()] if kb else None
    await event.message.answer(text=text, attachments=att, parse_mode=ParseMode.HTML)


async def callback_ack(
    bot: Bot,
    event: MessageCallback,
    notification: str | None = None,
) -> None:
    note = notification if notification else " "
    await bot.send_callback(
        callback_id=event.callback.callback_id,
        message=None,
        notification=note,
    )


async def edit_or_send_callback(
    bot: Bot,
    event: MessageCallback,
    text: str,
    kb: InlineKeyboardBuilder | None = None,
    parse_mode: ParseMode = ParseMode.HTML,
) -> None:
    if kb:
        att = [kb.as_markup()]
    else:
        from db.util import is_registered_bot_user
        from dispatcher_ref import user_has_fsm_state

        uid = event.callback.user.user_id
        if await is_registered_bot_user(uid) and not await user_has_fsm_state(uid):
            att = [main_menu_inline_button_kb().as_markup()]
        else:
            att = []
    try:
        if event.message and event.message.body:
            await bot.edit_message(
                message_id=event.message.body.mid,
                text=text,
                attachments=att,
                parse_mode=parse_mode,
            )
        else:
            raise ValueError("no body")
    except Exception:
        await bot.send_message(
            user_id=event.callback.user.user_id,
            text=text,
            attachments=att,
            parse_mode=parse_mode,
        )
    await callback_ack(bot, event)


async def send_user(
    bot: Bot,
    user_id: int,
    text: str,
    kb: InlineKeyboardBuilder | None = None,
    parse_mode: ParseMode = ParseMode.HTML,
    *,
    main_menu_attachment: bool | None = None,
) -> None:
    att = [kb.as_markup()] if kb else None
    await bot.send_message(
        user_id=user_id,
        text=text,
        attachments=att,
        parse_mode=parse_mode,
        main_menu_attachment=main_menu_attachment,
    )
