import datetime
import re
from typing import Optional

# Словарь для преобразования названий месяцев
MONTHS_MAP = {
    'января': 1, 'январь': 1,
    'февраля': 2, 'февраль': 2,
    'марта': 3, 'март': 3,
    'апреля': 4, 'апрель': 4,
    'мая': 5, 'май': 5,
    'июня': 6, 'июнь': 6,
    'июля': 7, 'июль': 7,
    'августа': 8, 'август': 8,
    'сентября': 9, 'сентябрь': 9,
    'октября': 10, 'октябрь': 10,
    'ноября': 11, 'ноябрь': 11,
    'декабря': 12, 'декабрь': 12
}


def parse_date(date_str: str) -> Optional[datetime.date]:
    """Парсит дату из строки в различных форматах"""
    date_str = date_str.strip().lower()
    now = datetime.datetime.now()

    # Формат ДД.ММ.ГГГГ
    try:
        return datetime.datetime.strptime(date_str, "%d.%m.%Y").date()
    except ValueError:
        pass

    # Формат ДД.ММ (текущий год)
    try:
        if re.match(r"^\d{1,2}\.\d{1,2}$", date_str):
            parsed = datetime.datetime.strptime(date_str, "%d.%m")
            return parsed.replace(year=now.year).date()
    except ValueError:
        pass

    # Текстовый формат (например, "3 апреля")
    match = re.match(r"^(\d{1,2})\s+([а-яё]+)$", date_str)
    if match:
        day_str, month_str = match.groups()
        try:
            day = int(day_str)
            month = MONTHS_MAP.get(month_str)
            if month:
                return now.replace(month=month, day=day).date()
        except ValueError:
            pass

    return None
