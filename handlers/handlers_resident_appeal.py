import asyncio

from maxapi import F, Router
from maxapi.types.attachments.buttons.callback_button import CallbackButton
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from bot import bot
from config import RAZRAB
from filters import IsResidentOrContractor
from max_helpers import callback_ack, send_user

router = Router(router_id="resident_appeal")
router.filter(IsResidentOrContractor())

UK_CONTACT_MESSAGE = (
    "Оставляйте обращения, пожелания, замечания\n\n"
    "zagorodomlife@outlook.com"
)


def _uk_contact_back_kb() -> InlineKeyboardBuilder:
    b = InlineKeyboardBuilder()
    b.row(CallbackButton(text="Назад", payload="back_to_main_menu"))
    return b


@router.message_callback(F.callback.payload == "appeals_menu")
async def appeals_menu(event: MessageCallback) -> None:
    try:
        await callback_ack(bot, event)
        await send_user(
            bot,
            event.callback.user.user_id,
            UK_CONTACT_MESSAGE,
            _uk_contact_back_kb(),
        )
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"{event.callback.user.user_id} - {e!s}")
        await asyncio.sleep(0.05)
