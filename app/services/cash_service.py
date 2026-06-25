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
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
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
    add_column_if_missing(cursor, columns, "estado", "ENUM('ABIERTA','CERRADA','ANULADA') NOT NULL DEFAULT 'ABIERTA'")
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

    cursor.execute("UPDATE caja_sesiones SET fecha_operacion = COALESCE(fecha_operacion, DATE(fecha_apertura), CURDATE()) WHERE fecha_operacion IS NULL")
    cursor.execute("UPDATE caja_sesiones SET abierta_at = COALESCE(abierta_at, fecha_apertura, created_at, NOW()) WHERE abierta_at IS NULL")
    cursor.execute("UPDATE caja_sesiones SET created_at = COALESCE(created_at, abierta_at, fecha_apertura, NOW()) WHERE created_at IS NULL")
    cursor.execute("UPDATE caja_sesiones SET monto_inicial_efectivo = COALESCE(monto_inicial_efectivo, monto_inicial, 0)")
    add_index_if_missing(cursor, "idx_caja_sesion_abierta", "(cliente_id, usuario_id, estado)")
    add_index_if_missing(cursor, "idx_caja_sesion_fecha", "(cliente_id, fecha_operacion)")


def get_location_context(cursor, ubicacion_stock_id):
    if not ubicacion_stock_id:
        return {}
    cursor.execute(
        """
        SELECT u.id, u.cliente_id, u.sucursal_id, u.almacen_id, u.nombre,
               COALESCE(s.nombre, a.nombre, u.nombre) AS ubicacion_nombre
        FROM ubicaciones_stock u
        LEFT JOIN sucursales s ON s.id=u.sucursal_id
        LEFT JOIN almacenes a ON a.id=u.almacen_id
        WHERE u.id=%s
        LIMIT 1
        """,
        (ubicacion_stock_id,),
    )
    return cursor.fetchone() or {}


def _estado_activo_for_column(column_type):
    text = str(column_type or "").upper()
    if "ACTIVA" in text:
        return "ACTIVA"
    if "ACTIVO" in text:
        return "ACTIVO"
    if "ABIERTA" in text:
        return "ABIERTA"
    return None


def _first_sucursal_id(cursor, cliente_id):
    cursor.execute("SELECT id FROM sucursales WHERE cliente_id=%s ORDER BY id ASC LIMIT 1", (cliente_id,))
    row = cursor.fetchone()
    return row["id"] if row else None


def create_cashbox(cursor, cliente_id, ubicacion_stock_id):
    if not table_exists(cursor, "cajas"):
        return None
    cols_info = column_info(cursor, "cajas")
    cols = set(cols_info.keys())
    location = get_location_context(cursor, ubicacion_stock_id)
    sucursal_id = location.get("sucursal_id") or _first_sucursal_id(cursor, cliente_id)
    if "sucursal_id" in cols and not sucursal_id:
        return None

    values = {}
    raw = set()
    if "cliente_id" in cols:
        values["cliente_id"] = cliente_id
    if "sucursal_id" in cols:
        values["sucursal_id"] = sucursal_id
    if "ubicacion_stock_id" in cols and ubicacion_stock_id:
        values["ubicacion_stock_id"] = ubicacion_stock_id
    if "nombre" in cols:
        values["nombre"] = f"Caja {location.get('ubicacion_nombre') or 'Principal'}"
    if "codigo" in cols:
        values["codigo"] = f"CAJA-{cliente_id}-{sucursal_id or ubicacion_stock_id or 'GEN'}"
    if "estado" in cols:
        estado = _estado_activo_for_column(cols_info["estado"].get("Type"))
        if estado:
            values["estado"] = estado
    if "created_at" in cols:
        values["created_at"] = "NOW()"
        raw.add("created_at")
    if "updated_at" in cols:
        values["updated_at"] = "NOW()"
        raw.add("updated_at")

    if not values:
        return None
    columns = list(values.keys())
    placeholders = []
    params = []
    for col in columns:
        if col in raw:
            placeholders.append(values[col])
        else:
            placeholders.append("%s")
            params.append(values[col])
    try:
        cursor.execute(f"INSERT INTO cajas ({', '.join(columns)}) VALUES ({', '.join(placeholders)})", tuple(params))
        return cursor.lastrowid
    except Exception:
        return None


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

    for condition, extra_params in candidates:
        cursor.execute(
            f"SELECT id FROM cajas WHERE {' AND '.join(filters)} AND {condition} ORDER BY id ASC LIMIT 1",
            tuple(params + extra_params),
        )
        row = cursor.fetchone()
        if row:
            return row["id"]

    created = create_cashbox(cursor, cliente_id, ubicacion_stock_id)
    if created:
        return created

    cursor.execute(f"SELECT id FROM cajas WHERE {' AND '.join(filters)} ORDER BY id ASC LIMIT 1", tuple(params))
    row = cursor.fetchone()
    return row["id"] if row else None


def _session_columns(cursor):
    return set(column_info(cursor, "caja_sesiones").keys())


def _user_open_filter(cols):
    parts = []
    if "usuario_id" in cols:
        parts.append("usuario_id=%s")
    if "usuario_apertura_id" in cols:
        parts.append("usuario_apertura_id=%s")
    return "(" + " OR ".join(parts or ["1=0"]) + ")"


def get_open_cash_session(cliente_id, usuario_id, ubicacion_stock_id=None):
    ensure_cash_tables()
    with db_cursor() as cursor:
        cols = _session_columns(cursor)
        params = [cliente_id]
        user_filter = _user_open_filter(cols)
        params.extend([usuario_id] * user_filter.count("%s"))
        location_filter = ""
        if ubicacion_stock_id and "ubicacion_stock_id" in cols:
            location_filter = " AND (ubicacion_stock_id=%s OR ubicacion_stock_id IS NULL)"
            params.append(ubicacion_stock_id)
        cursor.execute(
            f"""
            SELECT *
            FROM caja_sesiones
            WHERE cliente_id=%s AND {user_filter} AND estado='ABIERTA' {location_filter}
            ORDER BY COALESCE(abierta_at, fecha_apertura, created_at) DESC, id DESC
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
        cols = _session_columns(cursor)
        user_filter = _user_open_filter(cols)
        params = [cliente_id]
        params.extend([usuario_id] * user_filter.count("%s"))
        cursor.execute(
            f"SELECT id FROM caja_sesiones WHERE cliente_id=%s AND {user_filter} AND estado='ABIERTA' LIMIT 1 FOR UPDATE",
            tuple(params),
        )
        if cursor.fetchone():
            raise ValueError("Ya tienes una caja abierta. Debes cerrarla antes de abrir otra.")

        caja_id = find_cashbox_id(cursor, cliente_id, ubicacion_stock_id)
        values = {
            "cliente_id": cliente_id,
            "caja_id": caja_id,
            "usuario_id": usuario_id,
            "usuario_apertura_id": usuario_id,
            "ubicacion_stock_id": ubicacion_stock_id,
            "fecha_operacion": "CURDATE()",
            "fecha_apertura": "NOW()",
            "estado": "ABIERTA",
            "monto_inicial": monto_efectivo + monto_qr,
            "monto_inicial_efectivo": monto_efectivo,
            "monto_inicial_qr": monto_qr,
            "monto_final_sistema": 0,
            "abierta_at": "NOW()",
            "observacion": observacion,
            "observacion_apertura": observacion,
            "created_at": "NOW()",
            "updated_at": "NOW()",
        }
        raw = {"fecha_operacion", "fecha_apertura", "abierta_at", "created_at", "updated_at"}
        insert_cols = [col for col in values if col in cols]
        placeholders = []
        insert_params = []
        for col in insert_cols:
            if col in raw:
                placeholders.append(values[col])
            else:
                placeholders.append("%s")
                insert_params.append(values[col])
        cursor.execute(f"INSERT INTO caja_sesiones ({', '.join(insert_cols)}) VALUES ({', '.join(placeholders)})", tuple(insert_params))
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
        return {"ventas_efectivo": money(row.get("ventas_efectivo")), "ventas_qr": money(row.get("ventas_qr"))}


def cash_summary(cliente_id, usuario_id, ubicacion_stock_id=None):
    session = get_open_cash_session(cliente_id, usuario_id, ubicacion_stock_id)
    if not session:
        return None
    opened_at = session.get("abierta_at") or session.get("fecha_apertura") or session.get("created_at")
    sales = cash_sales_totals(cliente_id, usuario_id, session.get("ubicacion_stock_id"), opened_at)
    expected_cash = money(session.get("monto_inicial_efectivo") or session.get("monto_inicial")) + sales["ventas_efectivo"]
    expected_qr = money(session.get("monto_inicial_qr")) + sales["ventas_qr"]
    return {"session": session, "ventas_efectivo": sales["ventas_efectivo"], "ventas_qr": sales["ventas_qr"], "esperado_efectivo": expected_cash, "esperado_qr": expected_qr}


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
        cols = _session_columns(cursor)
        user_filter = _user_open_filter(cols)
        params = [cliente_id]
        params.extend([usuario_id] * user_filter.count("%s"))
        cursor.execute(
            f"SELECT * FROM caja_sesiones WHERE cliente_id=%s AND {user_filter} AND estado='ABIERTA' ORDER BY COALESCE(abierta_at, fecha_apertura, created_at) DESC, id DESC LIMIT 1 FOR UPDATE",
            tuple(params),
        )
        session = cursor.fetchone()
        if not session:
            raise ValueError("No tienes una caja abierta para cerrar.")
        opened_at = session.get("abierta_at") or session.get("fecha_apertura") or session.get("created_at")
        sales = cash_sales_totals(cliente_id, usuario_id, session.get("ubicacion_stock_id"), opened_at)
        expected_cash = money(session.get("monto_inicial_efectivo") or session.get("monto_inicial")) + sales["ventas_efectivo"]
        expected_qr = money(session.get("monto_inicial_qr")) + sales["ventas_qr"]
        expected_total = expected_cash + expected_qr
        final_total = final_cash + final_qr
        updates = {
            "estado": "CERRADA",
            "usuario_cierre_id": usuario_id,
            "fecha_cierre": "NOW()",
            "monto_esperado_efectivo": expected_cash,
            "monto_esperado_qr": expected_qr,
            "monto_final_efectivo": final_cash,
            "monto_final_qr": final_qr,
            "monto_final_sistema": expected_total,
            "monto_final_contado": final_total,
            "diferencia": final_total - expected_total,
            "cerrada_at": "NOW()",
            "observacion_cierre": observacion,
            "observacion": observacion,
            "updated_at": "NOW()",
        }
        raw = {"fecha_cierre", "cerrada_at", "updated_at"}
        set_parts = []
        update_params = []
        for col, value in updates.items():
            if col not in cols:
                continue
            if col in raw:
                set_parts.append(f"{col}={value}")
            else:
                set_parts.append(f"{col}=%s")
                update_params.append(value)
        update_params.append(session["id"])
        cursor.execute(f"UPDATE caja_sesiones SET {', '.join(set_parts)} WHERE id=%s", tuple(update_params))
        log_audit(cursor, cliente_id=cliente_id, usuario_id=usuario_id, modulo="CAJA", accion="CERRAR_CAJA", tabla_afectada="caja_sesiones", registro_id=session["id"], valor_anterior=session, valor_nuevo={"esperado_efectivo": str(expected_cash), "esperado_qr": str(expected_qr), "final_efectivo": str(final_cash), "final_qr": str(final_qr)})
        return {"esperado_efectivo": expected_cash, "esperado_qr": expected_qr, "final_efectivo": final_cash, "final_qr": final_qr}
