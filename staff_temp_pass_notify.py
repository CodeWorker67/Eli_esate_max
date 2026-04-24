"""Текст уведомлений СБ/менеджерам/админам об автоодобренных временных пропусках."""

from __future__ import annotations

import datetime
import html as html_lib
from typing import TYPE_CHECKING

from sqlalchemy import select

from temporary_truck import TRUCK_CATEGORY_LABELS

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from db.models import TemporaryPass


_OLD_TRUCK_CATEGORY: dict[tuple[str, str], str] = {
    ("light", "short"): "Малый грузовой (до 3,5 т), длина до 7 м",
    ("light", "long"): "Малый грузовой (до 3,5 т), длина свыше 7 м",
    ("heavy", "short"): "Грузовой свыше 3,5 т, длина до 7 м",
    ("heavy", "long"): "Грузовой свыше 3,5 т, длина свыше 7 м",
}


def _vehicle_category_label(tp: TemporaryPass) -> str | None:
    if (tp.vehicle_type or "") != "truck":
        return None
    wc = (tp.weight_category or "").strip()
    lc = (tp.length_category or "").strip()
    if wc in TRUCK_CATEGORY_LABELS:
        return wc
    key = (wc, lc)
    if key in _OLD_TRUCK_CATEGORY:
        return _OLD_TRUCK_CATEGORY[key]
    return None


def _vehicle_type_line(tp: TemporaryPass) -> str | None:
    if (tp.vehicle_type or "") == "truck":
        return "Грузовой"
    return None


def _visit_or_period_line(tp: TemporaryPass) -> str | None:
    vd = getattr(tp, "visit_date", None)
    if vd is None:
        return None
    ds = vd.strftime("%d.%m.%Y")
    purpose = (getattr(tp, "purpose", None) or "").strip()
    extra_days = 0
    if purpose.isdigit():
        extra_days = int(purpose)
    if extra_days <= 0:
        return f"Дата приезда — {ds}"
    end = vd + datetime.timedelta(days=extra_days)
    return f"Период действия — с {ds} по {end.strftime('%d.%m.%Y')}"


def staff_auto_approved_temp_pass_html(
    intro_html: str,
    tp: TemporaryPass,
    *,
    payment_rubles: int,
) -> str:
    """
    intro_html — первая строка (уже с HTML-разметкой, например fio_html).
    Остальные поля экранируются здесь.
    Строка «Оплата» добавляется только при payment_rubles > 0.
    """
    lines: list[str] = [intro_html]
    cat = _vehicle_category_label(tp)
    if cat:
        lines.append(f"Категория ТС — {html_lib.escape(cat)}")
    vtype = _vehicle_type_line(tp)
    if vtype:
        lines.append(f"Тип ТС — {html_lib.escape(vtype)}")
    if payment_rubles > 0:
        lines.append(f"Оплата — {payment_rubles} руб")
    brand = (tp.car_brand or "").strip()
    if brand:
        lines.append(f"Марка — {html_lib.escape(brand)}")
    num = (tp.car_number or "").strip()
    if num:
        lines.append(f"Номер — {html_lib.escape(num.upper())}")
    period = _visit_or_period_line(tp)
    if period:
        lines.append(html_lib.escape(period))
    return "\n".join(lines)


async def payment_rubles_for_temp_pass(session: AsyncSession, temporary_pass_id: int) -> int | None:
    from db.models import TempPassYooKassaPayment

    q = await session.execute(
        select(TempPassYooKassaPayment.amount_kopeks)
        .where(
            TempPassYooKassaPayment.temporary_pass_id == temporary_pass_id,
            TempPassYooKassaPayment.status == "succeeded",
        )
        .order_by(TempPassYooKassaPayment.paid_at.desc().nulls_last())
        .limit(1)
    )
    kopeks = q.scalar_one_or_none()
    if kopeks is None:
        return None
    return int(kopeks) // 100
