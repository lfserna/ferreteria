from datetime import datetime, timedelta
from decimal import Decimal

from app.database import db_cursor, db_transaction
from app.services.audit_service import log_audit

AUTO_CLOSE_NOTE = "Cierre automático sin comprobación de saldos."
_AUTO_CLOSE_RUNNING = False


def money(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


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


def index_exists(cursor, table_name, index_name):
    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND INDEX_NAME = %s
        """,
        (table_name, index_name),
    )
    row = cursor.fetchone() or {}
    return int(row.get("total") or 0) > 0


def add_column_if_missing(cursor, columns, table_name, name, definition):
    if name not in columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")
        columns.add(name)


def add_index_if_missing(cursor, table_name, index_name, definition):
    if not index_exists(cursor, table_name, index_name):
        cursor.execute(f"ALTER TABLE {table_name} ADD INDEX {index_name} {definition}")


def make_column_nullable(cursor, table_name, columns_info, name, definition):
    info = columns_info.get(name)
    if info and str(info.get("Null", "")).upper() == "NO" and info.get("Default") is None and name != "id":
        cursor.execute(f"ALTER TABLE {table_name} MODIFY COLUMN {name} {definition}")


def _coalesce_expr(cols, candidates, fallback="NULL"):
    existing = [col for col in candidates if col in cols]
    if not existing:
        return fallback
    return f"COALESCE({', '.join(existing)})" if len(existing) > 1 else existing[0]


def _session_user_expr(cols):
    return _coalesce_expr(cols, ["usuario_id", "usuario_apertura_id"], "NULL")


def _user_open_filter(cols):
    parts = []
    if "usuario_id" in cols:
        parts.append("usuario_id=%s")
    if "usuario_apertura_id" in cols:
        parts.append("usuario_apertura_id=%s")
    return "(" + " OR ".join(parts or ["1=0"]) + ")"


def _is_nullable(cols_info, name):
    info = cols_info.get(name) or {}
    return str(info.get("Null", "YES")).upper() == "YES"


def _nullable_or_value(cols_info, name, value):
    if name not in cols_info:
        return None
    return None if _is_nullable(cols_info, name) else value


def _opened_expr(cols):
    return _coalesce_expr(cols, ["abierta_at", "fecha_apertura", "created_at"], "created_at")


def _opened_value(session):
    return session.get("abierta_at") or session.get("fecha_apertura") or session.get("created_at")


def _auto_close_at(opened_at):
    if isinstance(opened_at, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                opened_at = datetime.strptime(opened_at, fmt)
                break
            except ValueError:
                pass
    if not hasattr(opened_at, "date"):
        return datetime.now()
    return datetime.combine(opened_at.date() + timedelta(days=1), datetime.min.time())


def ensure_sales_cash_session_column(cursor=None):
    def apply(cur):
        if not table_exists(cur, "ventas"):
            return
        cols = existing_columns(cur, "ventas")
        if "caja_sesion_id" not in cols:
            cur.execute("ALTER TABLE ventas ADD COLUMN caja_sesion_id BIGINT UNSIGNED NULL")
            cols.add("caja_sesion_id")
        add_index_if_missing(cur, "ventas", "idx_ventas_caja_sesion", "(cliente_id, caja_sesion_id)")
    if cursor is not None:
        apply(cursor)
    else:
        with db_cursor(commit=True) as cur:
            apply(cur)


def migrate_cash_session_table(cursor):
    cols = existing_columns(cursor, "caja_sesiones")
    add_column_if_missing(cursor, cols, "caja_sesiones", "cliente_id", "BIGINT UNSIGNED NOT NULL DEFAULT 1")
    add_column_if_missing(cursor, cols, "caja_sesiones", "usuario_id", "BIGINT UNSIGNED NOT NULL DEFAULT 0")
    add_column_if_missing(cursor, cols, "caja_sesiones", "ubicacion_stock_id", "BIGINT UNSIGNED NULL")
    add_column_if_missing(cursor, cols, "caja_sesiones", "fecha_operacion", "DATE NULL")
    add_column_if_missing(cursor, cols, "caja_sesiones", "estado", "ENUM('ABIERTA','CERRADA','ANULADA') NOT NULL DEFAULT 'ABIERTA'")
    add_column_if_missing(cursor, cols, "caja_sesiones", "monto_inicial_efectivo", "DECIMAL(12,2) NOT NULL DEFAULT 0")
    add_column_if_missing(cursor, cols, "caja_sesiones", "monto_inicial_qr", "DECIMAL(12,2) NOT NULL DEFAULT 0")
    add_column_if_missing(cursor, cols, "caja_sesiones", "monto_esperado_efectivo", "DECIMAL(12,2) NULL")
    add_column_if_missing(cursor, cols, "caja_sesiones", "monto_esperado_qr", "DECIMAL(12,2) NULL")
    add_column_if_missing(cursor, cols, "caja_sesiones", "monto_final_efectivo", "DECIMAL(12,2) NULL")
    add_column_if_missing(cursor, cols, "caja_sesiones", "monto_final_qr", "DECIMAL(12,2) NULL")
    add_column_if_missing(cursor, cols, "caja_sesiones", "diferencia_efectivo", "DECIMAL(12,2) NULL")
    add_column_if_missing(cursor, cols, "caja_sesiones", "diferencia_qr", "DECIMAL(12,2) NULL")
    add_column_if_missing(cursor, cols, "caja_sesiones", "cierre_automatico", "TINYINT(1) NOT NULL DEFAULT 0")
    add_column_if_missing(cursor, cols, "caja_sesiones", "abierta_at", "DATETIME NULL")
    add_column_if_missing(cursor, cols, "caja_sesiones", "cerrada_at", "DATETIME NULL")
    add_column_if_missing(cursor, cols, "caja_sesiones", "observacion_apertura", "TEXT NULL")
    add_column_if_missing(cursor, cols, "caja_sesiones", "observacion_cierre", "TEXT NULL")
    add_column_if_missing(cursor, cols, "caja_sesiones", "created_at", "DATETIME NULL")
    add_column_if_missing(cursor, cols, "caja_sesiones", "updated_at", "DATETIME NULL")

    info = column_info(cursor, "caja_sesiones")
    if "caja_id" in info:
        make_column_nullable(cursor, "caja_sesiones", info, "caja_id", "BIGINT UNSIGNED NULL")
    if "fecha_operacion" in cols:
        cursor.execute("UPDATE caja_sesiones SET fecha_operacion = COALESCE(fecha_operacion, DATE(COALESCE(fecha_apertura, abierta_at, created_at, NOW()))) WHERE fecha_operacion IS NULL")
    if "abierta_at" in cols:
        cursor.execute("UPDATE caja_sesiones SET abierta_at = COALESCE(abierta_at, fecha_apertura, created_at, NOW()) WHERE abierta_at IS NULL")
    if "created_at" in cols:
        cursor.execute("UPDATE caja_sesiones SET created_at = COALESCE(created_at, abierta_at, fecha_apertura, NOW()) WHERE created_at IS NULL")
    if "monto_inicial" in cols and "monto_inicial_efectivo" in cols:
        cursor.execute("UPDATE caja_sesiones SET monto_inicial_efectivo = COALESCE(monto_inicial_efectivo, monto_inicial, 0)")
    add_index_if_missing(cursor, "caja_sesiones", "idx_caja_sesion_abierta", "(cliente_id, usuario_id, estado)")
    add_index_if_missing(cursor, "caja_sesiones", "idx_caja_sesion_fecha", "(cliente_id, fecha_operacion)")


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
                diferencia_efectivo DECIMAL(12,2) NULL,
                diferencia_qr DECIMAL(12,2) NULL,
                cierre_automatico TINYINT(1) NOT NULL DEFAULT 0,
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
        ensure_sales_cash_session_column(cursor)
    if not _AUTO_CLOSE_RUNNING:
        auto_close_expired_cash_sessions(skip_ensure=True)


def get_location_context(cursor, ubicacion_stock_id):
    if not ubicacion_stock_id or not table_exists(cursor, "ubicaciones_stock"):
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


def _first_sucursal_id(cursor, cliente_id):
    if not table_exists(cursor, "sucursales"):
        return None
    cursor.execute("SELECT id FROM sucursales WHERE cliente_id=%s ORDER BY id ASC LIMIT 1", (cliente_id,))
    row = cursor.fetchone()
    return row["id"] if row else None


def _estado_activo_for_column(column_type):
    text = str(column_type or "").upper()
    if "ACTIVA" in text:
        return "ACTIVA"
    if "ACTIVO" in text:
        return "ACTIVO"
    return None


def create_cashbox(cursor, cliente_id, ubicacion_stock_id):
    if not table_exists(cursor, "cajas"):
        return None
    info = column_info(cursor, "cajas")
    cols = set(info.keys())
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
        estado = _estado_activo_for_column(info["estado"].get("Type"))
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
    names = list(values.keys())
    placeholders = []
    params = []
    for name in names:
        if name in raw:
            placeholders.append(values[name])
        else:
            placeholders.append("%s")
            params.append(values[name])
    try:
        cursor.execute(f"INSERT INTO cajas ({', '.join(names)}) VALUES ({', '.join(placeholders)})", tuple(params))
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
    for condition, extra in candidates:
        cursor.execute(f"SELECT id FROM cajas WHERE {' AND '.join(filters)} AND {condition} ORDER BY id ASC LIMIT 1", tuple(params + extra))
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


def attach_sales_to_session(cursor, cliente_id, usuario_id, session_id, ubicacion_stock_id, opened_at, closed_at=None):
    ensure_sales_cash_session_column(cursor)
    cols = existing_columns(cursor, "ventas")
    if "caja_sesion_id" not in cols:
        return
    params = [session_id, cliente_id, usuario_id, opened_at]
    filters = [
        "cliente_id=%s",
        "cajero_id=%s",
        "caja_sesion_id IS NULL",
        "estado='PAGADA'",
        "fecha_venta >= %s",
    ]
    if closed_at:
        filters.append("fecha_venta <= %s")
        params.append(closed_at)
    if ubicacion_stock_id:
        filters.append("(ubicacion_stock_id=%s OR ubicacion_stock_id IS NULL)")
        params.append(ubicacion_stock_id)
    cursor.execute(
        f"""
        UPDATE ventas
        SET caja_sesion_id=%s
        WHERE {' AND '.join(filters)}
        """,
        tuple(params),
    )


def select_cash_sales_totals(cursor, cliente_id, usuario_id, ubicacion_stock_id, opened_at, session_id=None, closed_at=None, attach=True):
    ensure_sales_cash_session_column(cursor)
    if session_id and attach:
        attach_sales_to_session(cursor, cliente_id, usuario_id, session_id, ubicacion_stock_id, opened_at, closed_at)
    cols = existing_columns(cursor, "ventas")
    params = [cliente_id]
    filters = ["v.cliente_id=%s", "v.estado='PAGADA'"]
    if session_id and "caja_sesion_id" in cols:
        filters.append("v.caja_sesion_id=%s")
        params.append(session_id)
        if closed_at:
            filters.append("v.fecha_venta <= %s")
            params.append(closed_at)
    else:
        filters.extend(["v.cajero_id=%s", "v.fecha_venta >= %s"])
        params.extend([usuario_id, opened_at])
        if ubicacion_stock_id:
            filters.append("(v.ubicacion_stock_id=%s OR v.ubicacion_stock_id IS NULL)")
            params.append(ubicacion_stock_id)
        if closed_at:
            filters.append("v.fecha_venta <= %s")
            params.append(closed_at)
    cursor.execute(
        f"""
        SELECT
            COALESCE(SUM(CASE WHEN vp.metodo_pago='EFECTIVO' THEN vp.monto ELSE 0 END),0) AS ventas_efectivo,
            COALESCE(SUM(CASE WHEN vp.metodo_pago='QR' THEN vp.monto ELSE 0 END),0) AS ventas_qr
        FROM ventas v
        JOIN venta_pagos vp ON vp.venta_id=v.id AND vp.cliente_id=v.cliente_id
        WHERE {' AND '.join(filters)}
        """,
        tuple(params),
    )
    row = cursor.fetchone() or {}
    return {"ventas_efectivo": money(row.get("ventas_efectivo")), "ventas_qr": money(row.get("ventas_qr"))}


def cash_sales_totals(cliente_id, usuario_id, ubicacion_stock_id, opened_at, session_id=None, closed_at=None):
    ensure_sales_cash_session_column()
    with db_cursor(commit=True) as cursor:
        return select_cash_sales_totals(cursor, cliente_id, usuario_id, ubicacion_stock_id, opened_at, session_id=session_id, closed_at=closed_at, attach=True)


def auto_close_expired_cash_sessions(cliente_id=None, skip_ensure=False):
    global _AUTO_CLOSE_RUNNING
    if _AUTO_CLOSE_RUNNING:
        return 0
    if not skip_ensure:
        ensure_cash_tables()
    _AUTO_CLOSE_RUNNING = True
    closed_count = 0
    try:
        with db_transaction() as (cursor, _connection):
            cols_info = column_info(cursor, "caja_sesiones")
            cols = set(cols_info.keys())
            opened_expr = _opened_expr(cols)
            user_expr = _session_user_expr(cols)
            filters = ["estado='ABIERTA'", f"DATE({opened_expr}) < CURDATE()"]
            params = []
            if cliente_id:
                filters.insert(0, "cliente_id=%s")
                params.append(cliente_id)
            cursor.execute(
                f"""
                SELECT *, {opened_expr} AS auto_opened_at, {user_expr} AS auto_usuario_id
                FROM caja_sesiones
                WHERE {' AND '.join(filters)}
                ORDER BY {opened_expr} ASC, id ASC
                FOR UPDATE
                """,
                tuple(params),
            )
            sessions = cursor.fetchall()
            for session in sessions:
                usuario_id = session.get("auto_usuario_id") or session.get("usuario_id") or session.get("usuario_apertura_id")
                if not usuario_id:
                    continue
                opened_at = session.get("auto_opened_at") or _opened_value(session)
                closed_at = _auto_close_at(opened_at)
                sales = select_cash_sales_totals(cursor, session["cliente_id"], usuario_id, session.get("ubicacion_stock_id"), opened_at, session_id=session["id"], closed_at=closed_at, attach=True)
                expected_cash = money(session.get("monto_inicial_efectivo") or session.get("monto_inicial")) + sales["ventas_efectivo"]
                expected_qr = money(session.get("monto_inicial_qr")) + sales["ventas_qr"]
                expected_total = expected_cash + expected_qr
                updates = {
                    "estado": "CERRADA",
                    "usuario_cierre_id": usuario_id,
                    "fecha_cierre": closed_at,
                    "cerrada_at": closed_at,
                    "monto_esperado_efectivo": expected_cash,
                    "monto_esperado_qr": expected_qr,
                    "monto_final_efectivo": _nullable_or_value(cols_info, "monto_final_efectivo", expected_cash),
                    "monto_final_qr": _nullable_or_value(cols_info, "monto_final_qr", expected_qr),
                    "diferencia_efectivo": _nullable_or_value(cols_info, "diferencia_efectivo", Decimal("0.00")),
                    "diferencia_qr": _nullable_or_value(cols_info, "diferencia_qr", Decimal("0.00")),
                    "monto_final_sistema": expected_total,
                    "monto_final_contado": _nullable_or_value(cols_info, "monto_final_contado", expected_total),
                    "diferencia": _nullable_or_value(cols_info, "diferencia", Decimal("0.00")),
                    "cierre_automatico": 1,
                    "observacion_cierre": AUTO_CLOSE_NOTE,
                    "observacion": AUTO_CLOSE_NOTE,
                    "updated_at": "NOW()",
                }
                raw = {"updated_at"}
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
                if not set_parts:
                    continue
                update_params.append(session["id"])
                cursor.execute(f"UPDATE caja_sesiones SET {', '.join(set_parts)} WHERE id=%s", tuple(update_params))
                log_audit(cursor, cliente_id=session["cliente_id"], usuario_id=usuario_id, modulo="CAJA", accion="CERRAR_CAJA_AUTOMATICO", tabla_afectada="caja_sesiones", registro_id=session["id"], valor_anterior=session, valor_nuevo={"observacion": AUTO_CLOSE_NOTE, "esperado_efectivo": str(expected_cash), "esperado_qr": str(expected_qr)})
                closed_count += 1
    finally:
        _AUTO_CLOSE_RUNNING = False
    return closed_count


def get_open_cash_session(cliente_id, usuario_id, ubicacion_stock_id=None):
    ensure_cash_tables()
    with db_cursor() as cursor:
        cols = _session_columns(cursor)
        user_filter = _user_open_filter(cols)
        params = [cliente_id]
        params.extend([usuario_id] * user_filter.count("%s"))
        location_filter = ""
        if ubicacion_stock_id and "ubicacion_stock_id" in cols:
            location_filter = " AND (ubicacion_stock_id=%s OR ubicacion_stock_id IS NULL)"
            params.append(ubicacion_stock_id)
        opened_expr = _opened_expr(cols)
        cursor.execute(
            f"""
            SELECT *
            FROM caja_sesiones
            WHERE cliente_id=%s AND {user_filter} AND estado='ABIERTA' {location_filter}
            ORDER BY {opened_expr} DESC, id DESC
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
        cursor.execute(f"SELECT id FROM caja_sesiones WHERE cliente_id=%s AND {user_filter} AND estado='ABIERTA' LIMIT 1 FOR UPDATE", tuple(params))
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
            "cierre_automatico": 0,
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


def cash_summary(cliente_id, usuario_id, ubicacion_stock_id=None):
    session = get_open_cash_session(cliente_id, usuario_id, ubicacion_stock_id)
    if not session:
        return None
    opened_at = _opened_value(session)
    sales = cash_sales_totals(cliente_id, usuario_id, session.get("ubicacion_stock_id"), opened_at, session_id=session.get("id"))
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
        opened_expr = _opened_expr(cols)
        cursor.execute(f"SELECT * FROM caja_sesiones WHERE cliente_id=%s AND {user_filter} AND estado='ABIERTA' ORDER BY {opened_expr} DESC, id DESC LIMIT 1 FOR UPDATE", tuple(params))
        session = cursor.fetchone()
        if not session:
            raise ValueError("No tienes una caja abierta para cerrar.")
        opened_at = _opened_value(session)
        sales = select_cash_sales_totals(cursor, cliente_id, usuario_id, session.get("ubicacion_stock_id"), opened_at, session_id=session["id"], attach=True)
        expected_cash = money(session.get("monto_inicial_efectivo") or session.get("monto_inicial")) + sales["ventas_efectivo"]
        expected_qr = money(session.get("monto_inicial_qr")) + sales["ventas_qr"]
        expected_total = expected_cash + expected_qr
        final_total = final_cash + final_qr
        diff_cash = final_cash - expected_cash
        diff_qr = final_qr - expected_qr
        diff_total = final_total - expected_total
        updates = {
            "estado": "CERRADA",
            "usuario_cierre_id": usuario_id,
            "fecha_cierre": "NOW()",
            "monto_esperado_efectivo": expected_cash,
            "monto_esperado_qr": expected_qr,
            "monto_final_efectivo": final_cash,
            "monto_final_qr": final_qr,
            "diferencia_efectivo": diff_cash,
            "diferencia_qr": diff_qr,
            "monto_final_sistema": expected_total,
            "monto_final_contado": final_total,
            "diferencia": diff_total,
            "cierre_automatico": 0,
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
        log_audit(cursor, cliente_id=cliente_id, usuario_id=usuario_id, modulo="CAJA", accion="CERRAR_CAJA", tabla_afectada="caja_sesiones", registro_id=session["id"], valor_anterior=session, valor_nuevo={"esperado_efectivo": str(expected_cash), "esperado_qr": str(expected_qr), "final_efectivo": str(final_cash), "final_qr": str(final_qr), "diferencia_efectivo": str(diff_cash), "diferencia_qr": str(diff_qr), "diferencia": str(diff_total)})
        return {"esperado_efectivo": expected_cash, "esperado_qr": expected_qr, "final_efectivo": final_cash, "final_qr": final_qr, "diferencia_efectivo": diff_cash, "diferencia_qr": diff_qr, "diferencia": diff_total}
