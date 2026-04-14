import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
import pandas as pd

from db import models


async def export_tables_to_excel():
    # Создаем асинхронное подключение к БД
    engine = create_async_engine(models.con_string)
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with AsyncSessionLocal() as session:
        # Получаем список всех таблиц из метаданных
        tables = models.Base.metadata.tables

        for table_name, table_obj in tables.items():
            # Формируем запрос для выбора всех данных из таблицы
            query = select(table_obj)
            result = await session.execute(query)
            rows = result.mappings().all()

            if rows:
                # Конвертируем в DataFrame
                df = pd.DataFrame(rows)

                # Сохраняем в Excel
                excel_file = f"export_import/{table_name}.xlsx"
                df.to_excel(excel_file, index=False)
                print(f"Таблица {table_name} экспортирована в {excel_file}")
            else:
                print(f"Таблица {table_name} пуста")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(export_tables_to_excel())