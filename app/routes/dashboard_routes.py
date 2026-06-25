from flask import Blueprint, g, redirect, render_template, url_for

from app.database import db_cursor
from app.utils.security import login_required


dashboard_bp = Blueprint("dashboard", __name__)


def role_code():
    return g.user.get("rol_codigo")


def sales_scope_clause(alias="v"):
    role = role_code()
    params = [g.user["cliente_id"]]
    clause = f"{alias}.cliente_id = %s"
    if role == "ADMIN_TIENDA":
        clause += f" AND {alias}.sucursal_id = %s"
        params.append(g.user.get("sucursal_id"))
    elif role == "CAJERO":
        clause += f" AND {alias}.cajero_id = %s"
        params.append(g.user["id"])
    elif role == "VENDEDOR":
        clause += f" AND {alias}.vendedor_id = %s"
        params.append(g.user["id"])
    return clause, params


def product_scope_clause(alias="p"):
    params = [g.user["cliente_id"]]
    return f"{alias}.cliente_id = %s", params


def stock_scope_clause(alias="i"):
    role = role_code()
    params = [g.user["cliente_id"]]
    clause = f"{alias}.cliente_id = %s"
    if role in {"ADMIN_TIENDA", "CAJERO", "VENDEDOR"} and g.user.get("sucursal_id"):
        clause += " AND us.sucursal_id = %s"
        params.append(g.user.get("sucursal_id"))
    return clause, params


def list_open_cash_sessions(cursor):
    role = role_code()
    if role not in {"ADMIN_GENERAL_NEGOCIO", "ADMIN_TIENDA"}:
        return []
    params = [g.user["cliente_id"]]
    store_filter = ""
    if role == "ADMIN_TIENDA":
        store_filter = "AND (us.sucursal_id = %s OR caj.sucursal_id = %s)"
        params.extend([g.user.get("sucursal_id"), g.user.get("sucursal_id")])
    cursor.execute(
        f"""
        SELECT
            cs.id,
            COALESCE(cs.usuario_id, cs.usuario_apertura_id) AS cajero_id,
            COALESCE(CONCAT_WS(' ', caj.nombres, caj.apellido_paterno), caj.username) AS cajero_nombre,
            caj.username AS cajero_username,
            COALESCE(us.nombre, s.nombre, 'Sin ubicación') AS ubicacion_nombre,
            COALESCE(s.nombre, suc_caj.nombre, 'Sin sucursal') AS sucursal_nombre,
            COALESCE(cs.abierta_at, cs.fecha_apertura, cs.created_at) AS abierta_at,
            COALESCE(cs.monto_inicial_efectivo, cs.monto_inicial, 0) AS monto_inicial_efectivo,
            COALESCE(cs.monto_inicial_qr, 0) AS monto_inicial_qr,
            COALESCE((
                SELECT SUM(vp.monto)
                FROM ventas v
                JOIN venta_pagos vp ON vp.venta_id = v.id AND vp.cliente_id = v.cliente_id
                WHERE v.cliente_id = cs.cliente_id
                  AND v.cajero_id = COALESCE(cs.usuario_id, cs.usuario_apertura_id)
                  AND v.estado = 'PAGADA'
                  AND v.fecha_venta >= COALESCE(cs.abierta_at, cs.fecha_apertura, cs.created_at)
                  AND (cs.ubicacion_stock_id IS NULL OR v.ubicacion_stock_id = cs.ubicacion_stock_id)
            ), 0) AS total_recaudado,
            COALESCE((
                SELECT SUM(CASE WHEN vp.metodo_pago='EFECTIVO' THEN vp.monto ELSE 0 END)
                FROM ventas v
                JOIN venta_pagos vp ON vp.venta_id = v.id AND vp.cliente_id = v.cliente_id
                WHERE v.cliente_id = cs.cliente_id
                  AND v.cajero_id = COALESCE(cs.usuario_id, cs.usuario_apertura_id)
                  AND v.estado = 'PAGADA'
                  AND v.fecha_venta >= COALESCE(cs.abierta_at, cs.fecha_apertura, cs.created_at)
                  AND (cs.ubicacion_stock_id IS NULL OR v.ubicacion_stock_id = cs.ubicacion_stock_id)
            ), 0) AS total_efectivo,
            COALESCE((
                SELECT SUM(CASE WHEN vp.metodo_pago='QR' THEN vp.monto ELSE 0 END)
                FROM ventas v
                JOIN venta_pagos vp ON vp.venta_id = v.id AND vp.cliente_id = v.cliente_id
                WHERE v.cliente_id = cs.cliente_id
                  AND v.cajero_id = COALESCE(cs.usuario_id, cs.usuario_apertura_id)
                  AND v.estado = 'PAGADA'
                  AND v.fecha_venta >= COALESCE(cs.abierta_at, cs.fecha_apertura, cs.created_at)
                  AND (cs.ubicacion_stock_id IS NULL OR v.ubicacion_stock_id = cs.ubicacion_stock_id)
            ), 0) AS total_qr
        FROM caja_sesiones cs
        LEFT JOIN usuarios caj ON caj.id = COALESCE(cs.usuario_id, cs.usuario_apertura_id)
        LEFT JOIN ubicaciones_stock us ON us.id = cs.ubicacion_stock_id
        LEFT JOIN sucursales s ON s.id = us.sucursal_id
        LEFT JOIN sucursales suc_caj ON suc_caj.id = caj.sucursal_id
        WHERE cs.cliente_id = %s
          AND cs.estado = 'ABIERTA'
          {store_filter}
        ORDER BY COALESCE(cs.abierta_at, cs.fecha_apertura, cs.created_at) DESC, cs.id DESC
        """,
        tuple(params),
    )
    return cursor.fetchall()


@dashboard_bp.route("/")
@login_required
def index():
    if g.user.get("rol_codigo") == "ADMIN_ALMACEN":
        return redirect(url_for("inventory.index"))
    cliente_id = g.user["cliente_id"]
    role = role_code()
    can_create_sale = role in {"ADMIN_TIENDA", "CAJERO", "VENDEDOR"}
    with db_cursor() as cursor:
        p_clause, p_params = product_scope_clause("p")
        cursor.execute(f"SELECT COUNT(*) AS total FROM productos p WHERE {p_clause} AND p.estado = 'ACTIVO'", tuple(p_params))
        productos = cursor.fetchone()["total"]

        v_clause, v_params = sales_scope_clause("v")
        cursor.execute(f"SELECT COALESCE(SUM(v.total),0) AS total FROM ventas v WHERE {v_clause} AND DATE(v.fecha_venta)=CURDATE()", tuple(v_params))
        ventas_hoy = cursor.fetchone()["total"]
        cursor.execute(f"SELECT COUNT(*) AS total FROM ventas v WHERE {v_clause} AND DATE(v.fecha_venta)=CURDATE()", tuple(v_params))
        transacciones_hoy = cursor.fetchone()["total"]

        s_clause, s_params = stock_scope_clause("i")
        cursor.execute(
            f"""
            SELECT COUNT(*) AS total
            FROM inventarios i
            LEFT JOIN ubicaciones_stock us ON us.id = i.ubicacion_stock_id
            WHERE {s_clause} AND i.cantidad_disponible <= i.cantidad_minima
            """,
            tuple(s_params),
        )
        stock_bajo = cursor.fetchone()["total"]

        cursor.execute("""
            SELECT titulo, mensaje, prioridad
            FROM alertas
            WHERE cliente_id = %s AND estado = 'ACTIVA'
              AND (fecha_inicio IS NULL OR fecha_inicio <= NOW())
              AND (fecha_fin IS NULL OR fecha_fin >= NOW())
            ORDER BY FIELD(prioridad, 'ALTA', 'MEDIA', 'BAJA'), created_at DESC
            LIMIT 5
        """, (cliente_id,))
        alertas = cursor.fetchall()

        cursor.execute(
            f"""
            SELECT v.id, v.numero_venta, v.numero_comprobante, v.fecha_venta, v.total,
                   s.nombre AS sucursal,
                   caj.username AS cajero,
                   ven.username AS vendedor
            FROM ventas v
            LEFT JOIN sucursales s ON s.id = v.sucursal_id
            LEFT JOIN usuarios caj ON caj.id = v.cajero_id
            LEFT JOIN usuarios ven ON ven.id = v.vendedor_id
            WHERE {v_clause}
            ORDER BY v.fecha_venta DESC
            LIMIT 8
            """,
            tuple(v_params),
        )
        ultimas_ventas = cursor.fetchall()
        cajas_abiertas = list_open_cash_sessions(cursor)
    return render_template(
        "dashboard.html",
        productos=productos,
        ventas_hoy=ventas_hoy,
        transacciones_hoy=transacciones_hoy,
        stock_bajo=stock_bajo,
        alertas=alertas,
        ultimas_ventas=ultimas_ventas,
        cajas_abiertas=cajas_abiertas,
        can_create_sale=can_create_sale,
        role=role,
    )
