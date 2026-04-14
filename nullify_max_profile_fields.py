"""
Обнуляет в SQLite поля MAX-профиля: tg_id, username, first_name, last_name.
Таблица user: только username, first_name, last_name (id — первичный ключ, не трогаем).

Запуск из корня проекта:
  python nullify_max_profile_fields.py
  python nullify_max_profile_fields.py --yes
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "db" / "database.db"

def _utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass


TABLE_COLUMNS: list[tuple[str, tuple[str, ...]]] = [
    ("resident", ("tg_id", "username", "first_name", "last_name")),
    ("contractor", ("tg_id", "username", "first_name", "last_name")),
    ("registration_request", ("tg_id", "username", "first_name", "last_name")),
    ("contractor_registration_request", ("tg_id", "username", "first_name", "last_name")),
    ("manager", ("tg_id", "username", "first_name", "last_name")),
    ("security", ("tg_id", "username", "first_name", "last_name")),
    ("user", ("username", "first_name", "last_name")),
]


def main() -> int:
    _utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Null tg_id/username/first_name/last_name in db/database.db (see docstring).",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )
    args = parser.parse_args()

    if not DB_PATH.is_file():
        print(f"Файл БД не найден: {DB_PATH}", file=sys.stderr)
        return 1

    if not args.yes:
        print(f"Будет изменён файл: {DB_PATH}")
        print("Обнуляются поля:", ", ".join("tg_id/username/first_name/last_name (где есть)"))
        ans = input("Продолжить? [y/N]: ").strip().lower()
        if ans not in ("y", "yes", "д", "да"):
            print("Отменено.")
            return 0

    con = sqlite3.connect(DB_PATH)
    try:
        cur = con.cursor()
        for table, cols in TABLE_COLUMNS:
            set_clause = ", ".join(f'"{c}" = NULL' for c in cols)
            cur.execute(f'UPDATE "{table}" SET {set_clause}')
            print(f"{table}: обновлено строк — {cur.rowcount}")
        con.commit()
    finally:
        con.close()

    print("Готово.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
