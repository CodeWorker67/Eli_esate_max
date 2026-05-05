"""Общие inline-клавиатуры резидента и подрядчика (без циклических импортов между handlers)."""

from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from max_helpers import inline_kb


def build_resident_main_kb() -> InlineKeyboardBuilder:
    return inline_kb(
        [
            [CallbackButton(text="Зарегистрировать подрядчика", payload="register_contractor")],
            [CallbackButton(text="Постоянные пропуска", payload="permanent_pass_menu")],
            [CallbackButton(text="Временные пропуска", payload="temporary_pass_menu")],
            [CallbackButton(text="Написать Руководителю УК", payload="appeals_menu")],
        ]
    )


resident_main_kb = build_resident_main_kb()


def contractor_main_menu_kb(can_add_contractor: bool) -> InlineKeyboardBuilder:
    uk_row = [CallbackButton(text="Написать Руководителю УК", payload="appeals_menu")]
    if can_add_contractor:
        return inline_kb(
            [
                [CallbackButton(text="Зарегистрировать субподрядчика", payload="register_contractor")],
                [CallbackButton(text="Временные пропуска", payload="temporary_pass_menu")],
                uk_row,
            ]
        )
    return inline_kb(
        [
            [CallbackButton(text="Временные пропуска", payload="temporary_pass_menu")],
            uk_row,
        ]
    )
