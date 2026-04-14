from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.attachments.buttons.link_button import LinkButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder


def create_kb(width: int, *args: str, **kwargs: str) -> InlineKeyboardBuilder:
    """
    Инлайн-клавиатура: kwargs — «payload: текст кнопки», в ряду по width кнопок.
    """
    b = InlineKeyboardBuilder()
    buttons: list[CallbackButton] = []
    for button_data, button_text in kwargs.items():
        buttons.append(CallbackButton(text=button_text, payload=button_data))
    for i in range(0, len(buttons), width):
        b.row(*buttons[i : i + width])
    return b


def kb_button(button_text: str, button_url: str) -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.row(LinkButton(text=button_text, url=button_url))
    return b
