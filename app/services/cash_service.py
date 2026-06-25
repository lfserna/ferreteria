from decimal import Decimal

from app.database import db_cursor, db_transaction
from app.services.audit_service import log_audit


def money(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


def ensure_cash_tables():
    with db_cursor(commit=True) as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS caja_sesiones (
                id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                cliente_id BIGINT UNSIGNED NOT NULL,
                usuario_id BIGINT UNSIGNED NOT NULL,
                ubicacion_stock_id BIGINT UNSIGNED NULL,
                fecha_operacion DATE NOT NULL,
                estado ENUM('ABIERTA','CERRADA') NOT NULL DEFAULT 'ABIERTA',
                monto_inicial_efectivo DECIMAL(12,2) NOT NULL DEFAULT 0,
                monto_inicial_qr DECIMAL(12,2) NOT NULL DEFAULT 0,
                monto_esperado_efectivo DECIMAL(12,2) NULL,
                monto_esperado_qr DECIMAL(12,2) NULL,
                monto_final_efectivo DECIMAL(12,2) NULL,
                monto_final_qr DECIMAL(12,2) NULL,
                abierta_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                cerrada_at DATETIME NULL,
                observacion_apertura TEXT NULL,
                observacion_cierre TEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NULL,
                INDEX idx_caja_sesion_abierta (cliente_id, usuario_id, estado),
                INDEX idx_caja_sesion_fecha (cliente_id, fecha_operacion)
            ) ENGINE=InnoDB
            """
        )
        migrate_cash_session_table(cursor)


def table_exists(cursor, table_name):
    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM INFORMATION_SCHEMA.TABLES
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
        """,
        (table_name,),
    )
    row = cursor.fetchone() or {}
    return int(row.get("total") or 0) > 0


def column_info(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    return {row["Field"]: row for row in cursor.fetchall()}


def existing_columns(cursor, table_name):
    return set(column_info(cursor, table_name).keys())


def add_column_if_missing(cursor, columns, name, definition):
    if name not in columns:
        cursor.execute(f"ALTER TABLE caja_sesiones ADD COLUMN {name} {definition}")
        columns.add(name)


def make_column_nullable(cursor, table_name, columns_info, name, definition):
    info = columns_info.get(name)
    if info and str(info.get("Null", "")).upper() == "NO" and info.get("Default") is None and name != "id":
        cursor.execute(f"ALTER TABLE {table_name} MODIFY COLUMN {name} {definition}")


def add_index_if_missing(cursor, index_name, definition):
    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = 'caja_sesiones'
          AND INDEX_NAME = %s
        """,
        (index_name,),
    )
    exists = cursor.fetchone()
    if not exists or int(exists.get("total") or 0) == 0:
        cursor.execute(f"ALTER TABLE caja_sesiones ADD INDEX {index_name} {definition}")


def migrate_cash_session_table(cursor):
    columns = existing_columns(cursor, "caja_sesiones")
    add_column_if_missing(cursor, columns, "cliente_id", "BIGINT UNSIGNED NOT NULL DEFAULT 1")
    add_column_if_missing(cursor, columns, "usuario_id", "BIGINT UNSIGNED NOT NULL DEFAULT 0")
    add_column_if_missing(cursor, columns, "ubicacion_stock_id", "BIGINT UNSIGNED NULL")
    add_column_if_missing(cursor, columns, "fecha_operacion", "DATE NULL")
    add_column_if_missing(cursor, columns, "estado", "ENUM('ABIERTA','CERRADA') NOT NULL DEFAULT 'ABIERTA'")
    add_column_if_missing(cursor, columns, "monto_inicial_efectivo", "DECIMAL(12,2) NOT NULL DEFAULT 0")
    add_column_if_missing(cursor, columns, "monto_inicial_qr", "DECIMAL(12,2) NOT NULL DEFAULT 0")
    add_column_if_missing(cursor, columns, "monto_esperado_efectivo", "DECIMAL(12,2) NULL")
    add_column_if_missing(cursor, columns, "monto_esperado_qr", "DECIMAL(12,2) NULL")
    add_column_if_missing(cursor, columns, "monto_final_efectivo", "DECIMAL(12,2) NULL")
    add_column_if_missing(cursor, columns, "monto_final_qr", "DECIMAL(12,2) NULL")
    add_column_if_missing(cursor, columns, "abierta_at", "DATETIME NULL")
    add_column_if_missing(cursor, columns, "cerrada_at", "DATETIME NULL")
    add_column_if_missing(cursor, columns, "observacion_apertura", "TEXT NULL")
    add_column_if_missing(cursor, columns, "observacion_cierre", "TEXT NULL")
    add_column_if_missing(cursor, columns, "created_at", "DATETIME NULL")
    add_column_if_missing(cursor, columns, "updated_at", "DATETIME NULL")
    info = column_info(cursor, "caja_sesiones")
    if "caja_id" in info:
        make_column_nullable(cursor, "caja_sesiones", info, "caja_id", "BIGINT UNSIGNED NULL")
    cursor.execute("UPDATE caja_sesiones SET fecha_operacion = COALESCE(fecha_operacion, CURDATE()) WHERE fecha_operacion IS NULL")
    cursor.execute("UPDATE caja_sesiones SET abierta_at = COALESCE(abierta_at, created_at, NOW()) WHERE abierta_at IS NULL")
    cursor.execute("UPDATE caja_sesiones SET created_at = COALESCE(created_at, abierta_at, NOW()) WHERE created_at IS NULL")
    add_index_if_missing(cursor, "idx_caja_sesion_abierta", "(cliente_id, usuario_id, estado)")
    add_index_if_missing(cursor, "idx_caja_sesion_fecha", "(cliente_id, fecha_operacion)")


def get_location_context(cursor, ubicacion_stock_id):
    if not ubicacion_stock_id:
        return {}
    cursor.execute(
        """
        SELECT id, cliente_id, sucursal_id, almacen_id, nombre
        FROM ubicaciones_stock
        WHERE id=%s
        LIMIT 1
        """,
        (ubicacion_stock_id,),
    )
    return cursor.fetchone() or {}


def find_cashbox_id(cursor, cliente_id, ubicacion_stock_id):
    if not table_exists(cursor, "cajas"):
        return None
    cols = existing_columns(cursor, "cajas")
    location = get_location_context(cursor, ubicacion_stock_id)
    filters = ["cliente_id=%s"] if "cliente_id" in cols else ["1=1"]
    params = [cliente_id] if "cliente_id" in cols else []

    candidates = []
    if "ubicacion_stock_id" in cols and ubicacion_stock_id:
        candidates.append(("ubicacion_stock_id=%s", [ubicacion_stock_id]))
    if "sucursal_id" in cols and location.get("sucursal_id"):
        candidates.append(("sucursal_id=%s", [location["sucursal_id"]]))
    if "almacen_id" in cols and location.get("almacen_id"):
        candidates.append(("almacen_id=%s", [location["almacen_id"]]))

    state_filter = ""
    if "estado" in cols:
        state_filter = " AND (estado='ACTIVO' OR estado='ABIERTA' OR estado IS NULL)"

    for condition, extra_params in candidates:
        cursor.execute(
            f"SELECT id FROM cajas WHERE {' AND '.join(filters)} AND {condition}{state_filter} ORDER BY id ASC LIMIT 1",
            tuple(params + extra_params),
        )
        row = cursor.fetchone()
        if row:
            return row["id"]

    cursor.execute(f"SELECT id FROM cajas WHERE {' AND '.join(filters)}{state_filter} ORDER BY id ASC LIMIT 1", tuple(params))
    row = cursor.fetchone()
    return row["id"] if row else None


def caja_id_column_exists(cursor):
    return "caja_id" in existing_columns(cursor, "caja_sesiones")


def get_open_cash_session(cliente_id, usuario_id, ubicacion_stock_id=None):
    ensure_cash_tables()
    params = [cliente_id, usuario_id]
    location_filter = ""
    if ubicacion_stock_id:
        location_filter = " AND (ubicacion_stock_id=%s OR ubicacion_stock_id IS NULL)"
        params.append(ubicacion_stock_id)
    with db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT *
            FROM caja_sesiones
            WHERE cliente_id=%s AND usuario_id=%s AND estado='ABIERTA' {location_filter}
            ORDER BY abierta_at DESC, id DESC
            LIMIT 1
            """,
            tuple(params),
        )
        return cursor.fetchone()


def open_cash_session(cliente_id, usuario_id, ubicacion_stock_id, monto_inicial_efectivo, monto_inicial_qr, observacion=None):
    ensure_cash_tables()
    monto_efectivo = money(monto_inicial_efectivo)
    monto_qr = money(monto_inicial_qr)
    if monto_efectivo < 0 or monto_qr < 0:
        raise ValueError("Los montos iniciales no pueden ser negativos.")
    with db_transaction() as (cursor, _connection):
        cursor.execute(
            """
            SELECT id FROM caja_sesiones
            WHERE cliente_id=%s AND usuario_id=%s AND estado='ABIERTA'
            FOR UPDATE
            """,
            (cliente_id, usuario_id),
        )
        existing = cursor.fetchone()
        if existing:
            raise ValueError("Ya tienes una caja abierta. Debes cerrarla antes de abrir otra.")

        insert_columns = [
            "cliente_id", "usuario_id", "ubicacion_stock_id", "fecha_operacion", "estado",
            "monto_inicial_efectivo", "monto_inicial_qr", "abierta_at", "observacion_apertura", "created_at", "updated_at"
        ]
        values = [cliente_id, usuario_id, ubicacion_stock_id, "CURDATE()", "ABIERTA", monto_efectivo, monto_qr, "NOW()", observacion, "NOW()", "NOW()"]
        raw_sql_positions = {3, 7, 9, 10}

        if caja_id_column_exists(cursor):
            insert_columns.insert(0, "caja_id")
            values.insert(0, find_cashbox_id(cursor, cliente_id, ubicacion_stock_id))
            raw_sql_positions = {index + 1 for index in raw_sql_positions}

        placeholders = []
        params = []
        for index, value in enumerate(values):
            if index in raw_sql_positions:
                placeholders.append(value)
            else:
                placeholders.append("%s")
                params.append(value)

        cursor.execute(
            f"INSERT INTO caja_sesiones ({', '.join(insert_columns)}) VALUES ({', '.join(placeholders)})",
            tuple(params),
        )
        session_id = cursor.lastrowid
        log_audit(cursor, cliente_id=cliente_id, usuario_id=usuario_id, modulo="CAJA", accion="ABRIR_CAJA", tabla_afectada="caja_sesiones", registro_id=session_id, valor_nuevo={"monto_inicial_efectivo": str(monto_efectivo), "monto_inicial_qr": str(monto_qr)})
        return session_id


def cash_sales_totals(cliente_id, usuario_id, ubicacion_stock_id, opened_at):
    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN vp.metodo_pago='EFECTIVO' THEN vp.monto ELSE 0 END),0) AS ventas_efectivo,
                COALESCE(SUM(CASE WHEN vp.metodo_pago='QR' THEN vp.monto ELSE 0 END),0) AS ventas_qr
            FROM ventas v
            JOIN venta_pagos vp ON vp.venta_id=v.id AND vp.cliente_id=v.cliente_id
            WHERE v.cliente_id=%s
              AND v.cajero_id=%s
              AND v.estado='PAGADA'
              AND v.fecha_venta >= %s
              AND (%s IS NULL OR v.ubicacion_stock_id=%s)
            """,
            (cliente_id, usuario_id, opened_at, ubicacion_stock_id, ubicacion_stock_id),
        )
        row = cursor.fetchone() or {}
        return {
            "ventas_efectivo": money(row.get("ventas_efectivo")),
            "ventas_qr": money(row.get("ventas_qr")),
        }


def cash_summary(cliente_id, usuario_id, ubicacion_stock_id=None):
    session = get_open_cash_session(cliente_id, usuario_id, ubicacion_stock_id)
    if not session:
        return None
    sales = cash_sales_totals(cliente_id, usuario_id, session.get("ubicacion_stock_id"), session["abierta_at"])
    expected_cash = money(session.get("monto_inicial_efectivo")) + sales["ventas_efectivo"]
    expected_qr = money(session.get("monto_inicial_qr")) + sales["ventas_qr"]
    return {
        "session": session,
        "ventas_efectivo": sales["ventas_efectivo"],
        "ventas_qr": sales["ventas_qr"],
        "esperado_efectivo": expected_cash,
        "esperado_qr": expected_qr,
    }


def require_open_cash(cliente_id, usuario_id, ubicacion_stock_id=None):
    summary = cash_summary(cliente_id, usuario_id, ubicacion_stock_id)
    if not summary:
        raise ValueError("Debes abrir caja antes de realizar ventas.")
    return summary


def close_cash_session(cliente_id, usuario_id, ubicacion_stock_id, monto_final_efectivo, monto_final_qr, observacion=None):
    ensure_cash_tables()
    final_cash = money(monto_final_efectivo)
    final_qr = money(monto_final_qr)
    if final_cash < 0 or final_qr < 0:
        raise ValueError("Los montos finales no pueden ser negativos.")
    with db_transaction() as (cursor, _connection):
        cursor.execute(
            """
            SELECT * FROM caja_sesiones
            WHERE cliente_id=%s AND usuario_id=%s AND estado='ABIERTA'
            ORDER BY abierta_at DESC, id DESC
            LIMIT 1
            FOR UPDATE
            """,
            (cliente_id, usuario_id),
        )
        session = cursor.fetchone()
        if not session:
            raise ValueError("No tienes una caja abierta para cerrar.")
        sales = cash_sales_totals(cliente_id, usuario_id, session.get("ubicacion_stock_id"), session["abierta_at"])
        expected_cash = money(session.get("monto_inicial_efectivo")) + sales["ventas_efectivo"]
        expected_qr = money(session.get("monto_inicial_qr")) + sales["ventas_qr"]
        cursor.execute(
            """
            UPDATE caja_sesiones
            SET estado='CERRADA', monto_esperado_efectivo=%s, monto_esperado_qr=%s,
                monto_final_efectivo=%s, monto_final_qr=%s, cerrada_at=NOW(), observacion_cierre=%s, updated_at=NOW()
            WHERE id=%s
            """,
            (expected_cash, expected_qr, final_cash, final_qr, observacion, session["id"]),
        )
        log_audit(cursor, cliente_id=cliente_id, usuario_id=usuario_id, modulo="CAJA", accion="CERRAR_CAJA", tabla_afectada="caja_sesiones", registro_id=session["id"], valor_anterior=session, valor_nuevo={"esperado_efectivo": str(expected_cash), "esperado_qr": str(expected_qr), "final_efectivo": str(final_cash), "final_qr": str(final_qr)})
        return {"esperado_efectivo": expected_cash, "esperado_qr": expected_qr, "final_efectivo": final_cash, "final_qr": final_qr}
