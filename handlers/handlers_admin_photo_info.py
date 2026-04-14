# handlers_admin_photo_info.py
"""Утилита для админов: по входящему фото вывести photo_id, token и url (MAX API)."""

import html as html_lib

from maxapi import Router
from maxapi.context.base import BaseContext
from maxapi.enums.attachment import AttachmentType
from maxapi.enums.parse_mode import ParseMode
from maxapi.filters.filter import BaseFilter
from maxapi.types.attachments.attachment import PhotoAttachmentPayload
from maxapi.types.attachments.image import Image
from maxapi.types.updates.message_created import MessageCreated

from bot import bot
from config import RAZRAB
from filters import IsAdmin
from max_helpers import user_id_from_message

router = Router(router_id="admin_photo_info")
router.filter(IsAdmin())


class HasImageAttachment(BaseFilter):
    async def __call__(self, event: object) -> bool:
        if not isinstance(event, MessageCreated):
            return False
        body = event.message.body
        if not body or not body.attachments:
            return False
        for att in body.attachments:
            if isinstance(att, Image):
                return True
            t = getattr(att, "type", None)
            if t == AttachmentType.IMAGE or t == "image":
                return True
        return False


def _photo_fields_from_attachment(att: object) -> tuple[int | None, str | None, str | None]:
    p = getattr(att, "payload", None)
    if isinstance(p, PhotoAttachmentPayload):
        return p.photo_id, p.token, p.url
    if p is None:
        return None, None, None
    pid = getattr(p, "photo_id", None)
    tok = getattr(p, "token", None) or None
    url = getattr(p, "url", None) or None
    if isinstance(pid, int):
        return pid, tok, url
    if pid is not None:
        try:
            return int(pid), tok, url
        except (TypeError, ValueError):
            return None, tok, url
    return None, tok, url


@router.message_created(IsAdmin(), HasImageAttachment(), states=[None])
async def admin_reply_photo_ids(event: MessageCreated, context: BaseContext) -> None:
    """По входящему фото от админа (вне любого сценария FSM) присылает photo_id, token, url."""
    if await context.get_state() is not None:
        return

    body = event.message.body
    if not body or not body.attachments:
        return

    uid = user_id_from_message(event)
    if uid is None:
        return

    lines: list[str] = []
    n = 0
    for att in body.attachments:
        t = getattr(att, "type", None)
        if not isinstance(att, Image) and t != AttachmentType.IMAGE and t != "image":
            continue
        n += 1
        photo_id, token, url = _photo_fields_from_attachment(att)
        lines.append(f"<b>Фото #{n}</b>")
        if photo_id is not None:
            lines.append(f"photo_id: <code>{photo_id}</code>")
        if token:
            lines.append(f"token: <code>{html_lib.escape(token)}</code>")
        if url:
            lines.append(f"url: {html_lib.escape(url)}")
        lines.append("")

    if not lines:
        return

    text = (
        "<b>Данные вложения (MAX)</b>\n\n"
        + "\n".join(lines).strip()
        + "\n\n<i>Для повторной отправки того же файла в API обычно передают "
        "вложение типа image с тем же token (и при необходимости photo_id).</i>"
    )

    try:
        await bot.send_message(user_id=uid, text=text, parse_mode=ParseMode.HTML)
    except Exception as e:
        await bot.send_message(user_id=RAZRAB, text=f"admin_photo_info: {uid} {e!s}")
