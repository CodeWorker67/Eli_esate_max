"""Ссылка на Dispatcher для проверки FSM вне обработчиков (например, в обёртке send_message)."""

from __future__ import annotations

from typing import Any

_dp: Any | None = None


def set_dispatcher(dp: Any | None) -> None:
    global _dp
    _dp = dp


def get_dispatcher() -> Any | None:
    return _dp


async def user_has_fsm_state(user_id: int | None) -> bool:
    if user_id is None:
        return False
    dp = get_dispatcher()
    if dp is None:
        return False
    for key, ctx in dp.contexts.items():
        if key[1] != user_id:
            continue
        if await ctx.get_state() is not None:
            return True
    return False
