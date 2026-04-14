from __future__ import annotations

from typing import TYPE_CHECKING

from maxapi.filters.filter import BaseFilter
from maxapi.types.updates.message_callback import MessageCallback
from maxapi.types.updates.message_created import MessageCreated
from sqlalchemy import select

from config import ADMIN_IDS
from db.models import (
    AsyncSessionLocal,
    Contractor,
    Manager,
    Resident,
    Security,
)

if TYPE_CHECKING:
    from maxapi.types.updates import UpdateUnion


def _user_id_from_event(event: UpdateUnion) -> int | None:
    from maxapi.types.updates.bot_started import BotStarted
    from maxapi.types.updates.bot_stopped import BotStopped

    if isinstance(event, MessageCreated):
        s = event.message.sender
        return s.user_id if s else None
    if isinstance(event, MessageCallback):
        return event.callback.user.user_id
    if isinstance(event, BotStarted) or isinstance(event, BotStopped):
        return event.user.user_id
    return None


class IsAdmin(BaseFilter):
    async def __call__(self, event: UpdateUnion) -> bool:
        uid = _user_id_from_event(event)
        return uid is not None and uid in ADMIN_IDS


class IsAdminOrManager(BaseFilter):
    async def __call__(self, event: UpdateUnion) -> bool:
        uid = _user_id_from_event(event)
        if uid is None:
            return False
        if uid in ADMIN_IDS:
            return True
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Manager).where(
                    Manager.tg_id == uid,
                    Manager.status == True,  # noqa: E712
                )
            )
            return result.scalar() is not None


class IsManager(BaseFilter):
    async def __call__(self, event: UpdateUnion) -> bool:
        uid = _user_id_from_event(event)
        if uid is None:
            return False
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Manager).where(
                    Manager.tg_id == uid,
                    Manager.status == True,  # noqa: E712
                )
            )
            return result.scalar() is not None


class IsSecurity(BaseFilter):
    async def __call__(self, event: UpdateUnion) -> bool:
        uid = _user_id_from_event(event)
        if uid is None:
            return False
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Security).where(
                    Security.tg_id == uid,
                    Security.status == True,  # noqa: E712
                )
            )
            return result.scalar() is not None


class IsResident(BaseFilter):
    async def __call__(self, event: UpdateUnion) -> bool:
        uid = _user_id_from_event(event)
        if uid is None:
            return False
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Resident).where(
                    Resident.tg_id == uid,
                    Resident.status == True,  # noqa: E712
                )
            )
            return result.scalar() is not None


class IsContractor(BaseFilter):
    async def __call__(self, event: UpdateUnion) -> bool:
        uid = _user_id_from_event(event)
        if uid is None:
            return False
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Contractor).where(
                    Contractor.tg_id == uid,
                    Contractor.status == True,  # noqa: E712
                )
            )
            return result.scalar() is not None
