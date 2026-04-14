import datetime
import re

from sqlalchemy import select, insert, update
from config import ADMIN_IDS
from db.models import User, AsyncSessionLocal, Manager, Security, Resident, Contractor


async def add_user_to_db(user_id, username, first_name, last_name, time_start):
    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(select(User).where(User.id == user_id))
            row = result.scalars().first()
            if not row:
                session.add(User(
                    id=user_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    time_start=time_start
                ))
            else:
                row.username = username
                row.first_name = first_name
                row.last_name = last_name
                row.time_start = time_start
            await session.commit()
        except Exception as e:
            print(e)
            await session.rollback()


async def update_user_blocked(id):
    async with AsyncSessionLocal() as session:
        try:
            stmt = update(User).where(User.id == id).values(is_active=False)
            await session.execute(stmt)
            await session.commit()
        except Exception as e:
            print(e)
            await session.rollback()


async def update_user_unblocked(id):
    async with AsyncSessionLocal() as session:
        try:
            stmt = update(User).where(User.id == id).values(is_active=True)
            await session.execute(stmt)
            await session.commit()
        except Exception as e:
            print(e)
            await session.rollback()


async def is_registered_bot_user(tg_id: int) -> bool:
    """Есть роль в боте: админ, активный менеджер/СБ, резидент или подрядчик (как в main_menu_inline)."""
    if tg_id in ADMIN_IDS:
        return True
    async with AsyncSessionLocal() as session:
        for model, extra_where in (
            (Manager, Manager.status == True),  # noqa: E712
            (Security, Security.status == True),  # noqa: E712
        ):
            r = await session.execute(
                select(model.id).where(model.tg_id == tg_id, extra_where).limit(1)
            )
            if r.scalar() is not None:
                return True
        for model in (Resident, Contractor):
            r = await session.execute(select(model.id).where(model.tg_id == tg_id).limit(1))
            if r.scalar() is not None:
                return True
    return False


async def is_active(user_id: int) -> bool:
    """
    Проверяет активность пользователя по его Telegram ID
    Возвращает значение поля is_active (True/False)
    Если пользователь не найден - возвращает False
    """
    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(
                select(User.is_active).where(User.id == int(user_id)))
            is_active = result.scalar_one_or_none()
            return bool(is_active)
        except Exception as e:
            print(f"Ошибка при проверке активности пользователя: {e}")
            return False


async def get_active_admins_and_managers_tg_ids() -> list[int]:
    """
    Получает список Telegram ID всех активных администраторов и менеджеров.

    Returns:
        list[int]: Список уникальных Telegram ID
    """
    async with AsyncSessionLocal() as session:

        # Запрос для менеджеров (статус True и заполненный tg_id)
        managers_query = select(Manager.tg_id).where(
            Manager.status == True,
            Manager.tg_id.isnot(None)
        )
        managers_result = await session.execute(managers_query)
        managers_ids = managers_result.scalars().all()

        # Объединение и удаление дубликатов
        all_ids = set(ADMIN_IDS) | set(managers_ids)
        return list(all_ids)


async def get_active_admins_managers_sb_tg_ids() -> list[int]:
    """
    Получает список Telegram ID всех активных администраторов и менеджеров.

    Returns:
        list[int]: Список уникальных Telegram ID
    """
    async with AsyncSessionLocal() as session:

        # Запрос для менеджеров (статус True и заполненный tg_id)
        managers_query = select(Manager.tg_id).where(
            Manager.status == True,
            Manager.tg_id.isnot(None)
        )
        managers_result = await session.execute(managers_query)
        managers_ids = managers_result.scalars().all()

        security_query = select(Security.tg_id).where(
            Security.status == True,
            Security.tg_id.isnot(None)
        )
        security_result = await session.execute(security_query)
        security_ids = security_result.scalars().all()

        # Объединение и удаление дубликатов
        all_ids = set(ADMIN_IDS) | set(managers_ids) | set(security_ids)
        return list(all_ids)

text_warning = '''
Уважаемые резиденты и подрядчики коттеджного поселка Ели Estate 🌲

‼️ЗАПРЕЩАЕТСЯ проезд по газонам, земле. Проезд только по дороге. Если нет возможности проехать, попросите отъехать или воспользуйтесь объездом.

🌲 резидент или подрядчик обязан встретить и сопроводить машину от ворот до пункта назначения. Иначе машина не заедет на территорию 

🌲грузовая машина выезжая с участка резидента, обязана вымыть свои колеса от грязи/песка/глины/земли. В противном случае, УК вправе выставить счет за загрязнение дороги

С уважением, УК Ели Estate 🌲
'''


async def get_all_users_unblock(status: str) -> list:
    """
    Получает список Telegram ID пользователей в зависимости от статуса

    Args:
        status:
            'users_1' - резиденты со статусом True
            'users_2' - подрядчики со статусом True
            'users_3' - резиденты и подрядчики со статусом True

    Returns:
        list: Список Telegram ID
    """
    async with AsyncSessionLocal() as session:
        if status == 'users_1':
            # Резиденты со статусом True
            query = select(Resident.tg_id).where(
                Resident.status == True
            )
            result = await session.execute(query)
            resident_ids = result.scalars().all()
            return list(resident_ids)

        elif status == 'users_2':
            # Подрядчики со статусом True
            query = select(Contractor.tg_id).where(
                Contractor.status == True
            )
            result = await session.execute(query)
            contractor_ids = result.scalars().all()
            return list(contractor_ids)

        elif status == 'users_3':
            # Резиденты и подрядчики со статусом True
            resident_query = select(Resident.tg_id).where(
                Resident.status == True
            )
            contractor_query = select(Contractor.tg_id).where(
                Contractor.status == True
            )

            resident_result = await session.execute(resident_query)
            contractor_result = await session.execute(contractor_query)

            resident_ids = resident_result.scalars().all()
            contractor_ids = contractor_result.scalars().all()

            # Объединяем и убираем дубликаты
            all_ids = set(resident_ids) | set(contractor_ids)
            return list(all_ids)

        else:
            return []
