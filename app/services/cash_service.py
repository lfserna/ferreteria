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
        cursor.execute(
            """
            INSERT INTO caja_sesiones
                (cliente_id, usuario_id, ubicacion_stock_id, fecha_operacion, estado,
                 monto_inicial_efectivo, monto_inicial_qr, abierta_at, observacion_apertura, created_at, updated_at)
            VALUES (%s,%s,%s,CURDATE(),'ABIERTA',%s,%s,NOW(),%s,NOW(),NOW())
            """,
            (cliente_id, usuario_id, ubicacion_stock_id, monto_efectivo, monto_qr, observacion),
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
