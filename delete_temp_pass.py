import asyncio
import datetime

from sqlalchemy import select, delete

from bot import bot
from db.models import TemporaryPass, AsyncSessionLocal



async def scheduler(day):
    await asyncio.sleep(5)
    old = day
    while True:
        if datetime.datetime.now().day != old:
            time_now = datetime.datetime.now().date()
            print(old, datetime.datetime.now().day)
            old = datetime.datetime.now().day
            async with AsyncSessionLocal() as session:
                stmt = select(TemporaryPass)
                result = await session.execute(stmt)
                passes = result.scalars().all()
                for temp_pass in passes:
                    days_ = temp_pass.purpose
                    days = 2
                    if days_.isdigit():
                        days = int(days_)
                    pass_end_date = temp_pass.visit_date + datetime.timedelta(days=days)
                    if pass_end_date < time_now:
                        pass_id = temp_pass.id
                        stmt = delete(TemporaryPass).where(TemporaryPass.id == pass_id)
                        await session.execute(stmt)
                        await session.commit()
        await asyncio.sleep(600)