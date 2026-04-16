import asyncio
import logging

from handlers import (
    handlers_admin_appeal,
    handlers_admin_manager_sending,
    handlers_admin_photo_info,
    handlers_admin_permanent_pass,
    handlers_admin_registration,
    handlers_admin_search,
    handlers_admin_self_pass,
    handlers_admin_statistic,
    handlers_admin_temporary_pass,
    handlers_admin_user_management,
    handlers_contractor,
    handlers_for_all,
    handlers_resident,
    handlers_resident_appeal,
    handlers_security,
    handlers_truck_yookassa,
)
from maxapi import Dispatcher

from bot import bot
from config import MAX_BOT_TOKEN
from dispatcher_ref import set_dispatcher
from db.models import create_tables

logger = logging.getLogger(__name__)


async def main() -> None:
    if not MAX_BOT_TOKEN:
        raise RuntimeError("Задайте MAX_BOT_TOKEN в .env (токен бота MAX).")

    await create_tables()
    logging.basicConfig(
        level=logging.INFO,
        format="%(filename)s:%(lineno)d %(levelname)-8s [%(asctime)s] - %(name)s - %(message)s",
    )
    logging.info("Starting MAX bot")

    dp = Dispatcher()
    set_dispatcher(dp)
    dp.include_routers(
        handlers_admin_user_management.router,
        handlers_admin_registration.router,
        handlers_admin_self_pass.router,
        handlers_admin_appeal.router,
        handlers_admin_search.router,
        handlers_admin_statistic.router,
        handlers_admin_permanent_pass.router,
        handlers_admin_temporary_pass.router,
        handlers_admin_manager_sending.router,
        handlers_admin_photo_info.router,
        handlers_security.router,
        handlers_truck_yookassa.router,
        handlers_contractor.router,
        handlers_resident.router,
        handlers_resident_appeal.router,
        handlers_for_all.router,
    )

    try:
        await dp.start_polling(bot, skip_updates=True)
    finally:
        await bot.close_session()


if __name__ == "__main__":
    asyncio.run(main())
