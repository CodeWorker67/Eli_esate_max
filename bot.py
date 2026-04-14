import types

from maxapi import Bot

from config import MAX_BOT_TOKEN, RAZRAB

# Токен из MAX_BOT_TOKEN (см. .env), как в библиотеке maxapi.
bot = Bot(MAX_BOT_TOKEN)

_original_send_message = type(bot).send_message


async def _send_message_with_main_menu_button(self, *args, **kwargs):
    main_menu_attachment = kwargs.pop("main_menu_attachment", None)

    user_id = kwargs.get("user_id")
    attachments = kwargs.get("attachments")

    if attachments is None and user_id is not None and user_id != RAZRAB:
        from db.util import is_registered_bot_user
        from dispatcher_ref import user_has_fsm_state
        from max_helpers import main_menu_inline_button_kb

        add = False
        if main_menu_attachment is False:
            add = False
        elif main_menu_attachment is True:
            add = await is_registered_bot_user(user_id)
        else:
            add = await is_registered_bot_user(user_id) and not await user_has_fsm_state(user_id)

        if add:
            kwargs = {**kwargs, "attachments": [main_menu_inline_button_kb().as_markup()]}

    return await _original_send_message(self, *args, **kwargs)


bot.send_message = types.MethodType(_send_message_with_main_menu_button, bot)
