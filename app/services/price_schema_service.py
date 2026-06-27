from decimal import Decimal

from app.database import db_cursor

EXTRA_PRICE_FIELDS = {
    "precio_cuarta": "Precio 1/4",
    "precio_media": "Precio 1/2",
    "precio_docena": "Precio docena",
    "precio_caja": "Precio caja",
}


def table_columns(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    return {row["Field"] for row in cursor.fetchall()}


def ensure_extra_price_columns(cursor=None):
    if cursor is None:
        with db_cursor(commit=True) as c:
            return ensure_extra_price_columns(c)
    cols = table_columns(cursor, "producto_precios")
    for field in EXTRA_PRICE_FIELDS:
        if field not in cols:
            cursor.execute(f"ALTER TABLE producto_precios ADD COLUMN {field} DECIMAL(12,2) NULL")
            cols.add(field)
    return cols


def money_or_none(value):
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value))
    except Exception as exc:
        raise ValueError("Los precios deben ser números válidos.") from exc
    if amount < 0:
        raise ValueError("Los precios no pueden ser negativos.")
    return amount.quantize(Decimal("0.01"))


def extra_prices_from_data(data):
    return {field: money_or_none(data.get(field)) for field in EXTRA_PRICE_FIELDS}
