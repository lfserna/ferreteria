from datetime import date, datetime, timedelta
from io import BytesIO

from flask import Blueprint, g, make_response, redirect, render_template, request, url_for
from xhtml2pdf import pisa

from app.database import db_cursor
from app.services.cash_service import ensure_cash_tables
from app.utils.security import login_required


dashboard_bp = Blueprint("dashboard", __name__)


def role_code():
    return g.user.get("rol_codigo")


def user_full_name(user=None):
    user = user or g.user
    parts = [user.get("nombres"), user.get("apellido_paterno")]
    name = " ".join([part for part in parts if part])
    return name or user.get("username") or "-"


def table_columns(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    return {row["Field"] for row in cursor.fetchall()}


def _column_from_expr(expr):
    token = str(expr).split(".")[-1]
    token = token.replace("`", "").strip()
    return token


def coalesce_existing(cols, candidates, fallback="NULL"):
    existing = [candidate for candidate in candidates if _column_from_expr(candidate) in cols]
    if not existing:
        return fallback
    return f"COALESCE({', '.join(existing)})" if len(existing) > 1 else existing[0]


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
    return f"{alias}.cliente_id = %s", [g.user["cliente_id"]]


def stock_scope_clause(alias="i"):
    role = role_code()
    params = [g.user["cliente_id"]]
    clause = f"{alias}.cliente_id = %s"
    if role in {"ADMIN_TIENDA", "CAJERO", "VENDEDOR"} and g.user.get("sucursal_id"):
        clause += " AND us.sucursal_id = %s"
        params.append(g.user.get("sucursal_id"))
    return clause, params


def list_open_cash_sessions(cursor):
    ensure_cash_tables()
    role = role_code()
    if role not in {"ADMIN_GENERAL_NEGOCIO", "ADMIN_TIENDA"}:
        return []
    params = [g.user["cliente_id"]]
    store_filter = ""
    if role == "ADMIN_TIENDA":
        store_filter = "AND (us.sucursal_id = %s OR caj.sucursal_id = %s)"
        params.extend([g.user.get("sucursal_id"), g.user.get("sucursal_id")])
    ventas_cols = table_columns(cursor, "ventas")
    if "caja_sesion_id" in ventas_cols:
        sale_match = "(v.caja_sesion_id = cs.id OR (v.caja_sesion_id IS NULL AND v.cajero_id = COALESCE(cs.usuario_id, cs.usuario_apertura_id) AND v.fecha_venta >= COALESCE(cs.abierta_at, cs.fecha_apertura, cs.created_at) AND (cs.ubicacion_stock_id IS NULL OR v.ubicacion_stock_id = cs.ubicacion_stock_id)))"
    else:
        sale_match = "(v.cajero_id = COALESCE(cs.usuario_id, cs.usuario_apertura_id) AND v.fecha_venta >= COALESCE(cs.abierta_at, cs.fecha_apertura, cs.created_at) AND (cs.ubicacion_stock_id IS NULL OR v.ubicacion_stock_id = cs.ubicacion_stock_id))"
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
            COALESCE((SELECT SUM(vp.monto) FROM ventas v JOIN venta_pagos vp ON vp.venta_id = v.id AND vp.cliente_id = v.cliente_id WHERE v.cliente_id = cs.cliente_id AND v.estado = 'PAGADA' AND {sale_match}), 0) AS total_recaudado,
            COALESCE((SELECT SUM(CASE WHEN vp.metodo_pago='EFECTIVO' THEN vp.monto ELSE 0 END) FROM ventas v JOIN venta_pagos vp ON vp.venta_id = v.id AND vp.cliente_id = v.cliente_id WHERE v.cliente_id = cs.cliente_id AND v.estado = 'PAGADA' AND {sale_match}), 0) AS total_efectivo,
            COALESCE((SELECT SUM(CASE WHEN vp.metodo_pago='QR' THEN vp.monto ELSE 0 END) FROM ventas v JOIN venta_pagos vp ON vp.venta_id = v.id AND vp.cliente_id = v.cliente_id WHERE v.cliente_id = cs.cliente_id AND v.estado = 'PAGADA' AND {sale_match}), 0) AS total_qr
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


def list_dashboard_sucursales(cursor):
    role = role_code()
    params = [g.user["cliente_id"]]
    where = "cliente_id=%s"
    if role == "ADMIN_TIENDA":
        where += " AND id=%s"
        params.append(g.user.get("sucursal_id"))
    cursor.execute(f"SELECT id, nombre FROM sucursales WHERE {where} ORDER BY nombre", tuple(params))
    return cursor.fetchall()


def list_dashboard_cashiers(cursor):
    role = role_code()
    params = [g.user["cliente_id"]]
    store_filter = ""
    if role == "ADMIN_TIENDA":
        store_filter = "AND (u.sucursal_id=%s OR ur.sucursal_id=%s)"
        params.extend([g.user.get("sucursal_id"), g.user.get("sucursal_id")])
    cursor.execute(
        f"""
        SELECT DISTINCT u.id, u.username, u.nombres, u.apellido_paterno
        FROM usuarios u
        JOIN usuario_roles ur ON ur.usuario_id=u.id AND ur.cliente_id=u.cliente_id AND ur.estado='ACTIVO'
        JOIN roles r ON r.id=ur.rol_id AND r.estado='ACTIVO'
        WHERE u.cliente_id=%s
          AND u.estado='ACTIVO'
          AND r.codigo IN ('CAJERO','ADMIN_TIENDA')
          {store_filter}
        ORDER BY u.nombres, u.apellido_paterno, u.username
        """,
        tuple(params),
    )
    rows = cursor.fetchall()
    for row in rows:
        row["nombre_visible"] = user_full_name(row)
    return rows


def resolve_period(args):
    today = date.today()
    period = (args.get("periodo") or "DIA").upper()
    if period == "SEMANA":
        start = today - timedelta(days=6)
        end = today
    elif period == "MES":
        start = today.replace(day=1)
        end = today
    elif period == "TRES_MESES":
        start = today - timedelta(days=89)
        end = today
    elif period == "RANGO":
        try:
            start = datetime.strptime(args.get("fecha_inicio") or "", "%Y-%m-%d").date()
            end = datetime.strptime(args.get("fecha_fin") or "", "%Y-%m-%d").date()
        except ValueError:
            start = today
            end = today
        if end < start:
            start, end = end, start
    else:
        period = "DIA"
        start = today
        end = today
    return period, start, end


def cash_history_report_rows(cursor, start, end, sucursal_id=None, cajero_id=None):
    ensure_cash_tables()
    cols = table_columns(cursor, "caja_sesiones")
    ventas_cols = table_columns(cursor, "ventas")
    opened_expr = coalesce_existing(cols, ["cs.abierta_at", "cs.fecha_apertura", "cs.created_at"], "cs.created_at")
    closed_expr = coalesce_existing(cols, ["cs.cerrada_at", "cs.fecha_cierre", "cs.updated_at"], "cs.updated_at")
    cash_user_expr = "COALESCE(cs.usuario_id, cs.usuario_apertura_id)" if "usuario_apertura_id" in cols else "cs.usuario_id"
    cash_location_expr = "cs.ubicacion_stock_id" if "ubicacion_stock_id" in cols else "NULL"
    initial_cash_expr = "COALESCE(cs.monto_inicial_efectivo, cs.monto_inicial, 0)" if "monto_inicial" in cols else "COALESCE(cs.monto_inicial_efectivo, 0)"
    initial_qr_expr = "COALESCE(cs.monto_inicial_qr, 0)" if "monto_inicial_qr" in cols else "0"
    final_cash_expr = "COALESCE(cs.monto_final_efectivo, 0)" if "monto_final_efectivo" in cols else "0"
    final_qr_expr = "COALESCE(cs.monto_final_qr, 0)" if "monto_final_qr" in cols else "0"
    if "diferencia" in cols:
        diff_expr = "COALESCE(cs.diferencia, 0)"
    elif "diferencia_efectivo" in cols or "diferencia_qr" in cols:
        diff_expr = "COALESCE(cs.diferencia_efectivo,0)+COALESCE(cs.diferencia_qr,0)"
    else:
        diff_expr = f"(({final_cash_expr}) + ({final_qr_expr}) - COALESCE(cs.monto_final_sistema, 0))"
    if "caja_sesion_id" in ventas_cols:
        sale_match = f"(v.caja_sesion_id = cs.id OR (v.caja_sesion_id IS NULL AND v.cajero_id={cash_user_expr} AND v.fecha_venta >= {opened_expr} AND ({closed_expr} IS NULL OR v.fecha_venta <= {closed_expr}) AND ({cash_location_expr} IS NULL OR v.ubicacion_stock_id={cash_location_expr})))"
    else:
        sale_match = f"(v.cajero_id={cash_user_expr} AND v.fecha_venta >= {opened_expr} AND ({closed_expr} IS NULL OR v.fecha_venta <= {closed_expr}) AND ({cash_location_expr} IS NULL OR v.ubicacion_stock_id={cash_location_expr}))"

    params = [g.user["cliente_id"], start, end + timedelta(days=1)]
    filters = ["cs.cliente_id=%s", f"{opened_expr} >= %s", f"{opened_expr} < %s"]
    role = role_code()
    if role == "ADMIN_TIENDA":
        filters.append("(us.sucursal_id=%s OR caj.sucursal_id=%s)")
        params.extend([g.user.get("sucursal_id"), g.user.get("sucursal_id")])
    if sucursal_id:
        filters.append("(us.sucursal_id=%s OR caj.sucursal_id=%s)")
        params.extend([sucursal_id, sucursal_id])
    if cajero_id:
        filters.append(f"{cash_user_expr}=%s")
        params.append(cajero_id)

    cursor.execute(
        f"""
        SELECT
            cs.id AS numero_caja,
            COALESCE(s.nombre, suc_caj.nombre, 'Sin sucursal') AS sucursal,
            {cash_user_expr} AS cajero_id,
            COALESCE(CONCAT_WS(' ', caj.nombres, caj.apellido_paterno), caj.username) AS cajero,
            {opened_expr} AS abierto,
            {closed_expr} AS cerrado,
            {initial_cash_expr} AS apertura_efectivo,
            {initial_qr_expr} AS apertura_qr,
            {final_cash_expr} AS cierre_efectivo,
            {final_qr_expr} AS cierre_qr,
            {diff_expr} AS diferencia,
            COALESCE((SELECT SUM(vp.monto) FROM ventas v JOIN venta_pagos vp ON vp.venta_id=v.id AND vp.cliente_id=v.cliente_id WHERE v.cliente_id=cs.cliente_id AND v.estado='PAGADA' AND {sale_match}), 0) AS total_recaudado
        FROM caja_sesiones cs
        LEFT JOIN usuarios caj ON caj.id={cash_user_expr}
        LEFT JOIN ubicaciones_stock us ON us.id={cash_location_expr}
        LEFT JOIN sucursales s ON s.id=us.sucursal_id
        LEFT JOIN sucursales suc_caj ON suc_caj.id=caj.sucursal_id
        WHERE {' AND '.join(filters)}
        ORDER BY {opened_expr} DESC, cs.id DESC
        """,
        tuple(params),
    )
    return cursor.fetchall()


def sales_report_rows(cursor, start, end, sucursal_id=None, cajero_id=None):
    params = [g.user["cliente_id"], start, end + timedelta(days=1)]
    filters = ["v.cliente_id=%s", "v.fecha_venta >= %s", "v.fecha_venta < %s"]
    if role_code() == "ADMIN_TIENDA":
        filters.append("v.sucursal_id=%s")
        params.append(g.user.get("sucursal_id"))
    elif sucursal_id:
        filters.append("v.sucursal_id=%s")
        params.append(sucursal_id)
    if cajero_id:
        filters.append("v.cajero_id=%s")
        params.append(cajero_id)
    cursor.execute(
        f"""
        SELECT
            v.id,
            v.numero_venta,
            v.numero_comprobante,
            v.fecha_venta,
            v.estado,
            COALESCE(s.nombre, 'Sin sucursal') AS sucursal,
            COALESCE(CONCAT_WS(' ', caj.nombres, caj.apellido_paterno), caj.username, '-') AS cajero,
            COALESCE(CONCAT_WS(' ', ven.nombres, ven.apellido_paterno), ven.username, '-') AS vendedor,
            COALESCE((SELECT GROUP_CONCAT(DISTINCT vp.metodo_pago ORDER BY vp.metodo_pago SEPARATOR ', ') FROM venta_pagos vp WHERE vp.cliente_id=v.cliente_id AND vp.venta_id=v.id), '-') AS metodo_pago,
            COALESCE(v.subtotal, 0) AS subtotal,
            COALESCE(v.descuento_total, 0) AS descuento_total,
            COALESCE(v.total, 0) AS total
        FROM ventas v
        LEFT JOIN sucursales s ON s.id=v.sucursal_id
        LEFT JOIN usuarios caj ON caj.id=v.cajero_id
        LEFT JOIN usuarios ven ON ven.id=v.vendedor_id
        WHERE {' AND '.join(filters)}
        ORDER BY v.fecha_venta DESC, v.id DESC
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
        sucursales_reporte = list_dashboard_sucursales(cursor) if role in {"ADMIN_GENERAL_NEGOCIO", "ADMIN_TIENDA"} else []
        cajeros_reporte = list_dashboard_cashiers(cursor) if role in {"ADMIN_GENERAL_NEGOCIO", "ADMIN_TIENDA"} else []
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
        sucursales_reporte=sucursales_reporte,
        cajeros_reporte=cajeros_reporte,
    )


@dashboard_bp.route("/cajas/historial/pdf")
@login_required
def cash_history_pdf():
    if role_code() not in {"ADMIN_GENERAL_NEGOCIO", "ADMIN_TIENDA"}:
        return redirect(url_for("dashboard.index"))
    period, start, end = resolve_period(request.args)
    sucursal_id = int(request.args.get("sucursal_id") or 0) or None
    cajero_id = int(request.args.get("cajero_id") or 0) or None
    if role_code() == "ADMIN_TIENDA" and sucursal_id and sucursal_id != g.user.get("sucursal_id"):
        sucursal_id = g.user.get("sucursal_id")
    with db_cursor() as cursor:
        rows = cash_history_report_rows(cursor, start, end, sucursal_id=sucursal_id, cajero_id=cajero_id)
        sucursales = list_dashboard_sucursales(cursor)
        cajeros = list_dashboard_cashiers(cursor)
    sucursal_name = next((s["nombre"] for s in sucursales if sucursal_id and int(s["id"]) == int(sucursal_id)), "Todas" if role_code() == "ADMIN_GENERAL_NEGOCIO" else (g.user.get("sucursal_nombre") or "Mi tienda"))
    cajero_name = next((c["nombre_visible"] for c in cajeros if cajero_id and int(c["id"]) == int(cajero_id)), "Todos")
    totals = {"diferencia": sum((r.get("diferencia") or 0) for r in rows), "total_recaudado": sum((r.get("total_recaudado") or 0) for r in rows)}
    html = render_template(
        "reports/cash_history_pdf.html",
        rows=rows,
        totals=totals,
        periodo=period,
        fecha_inicio=start,
        fecha_fin=end,
        generado_en=datetime.now(),
        generado_por=user_full_name(),
        cliente=g.user.get("cliente_nombre"),
        sucursal_name=sucursal_name,
        cajero_name=cajero_name,
    )
    pdf_buffer = BytesIO()
    status = pisa.CreatePDF(html, dest=pdf_buffer, encoding="UTF-8")
    if status.err:
        return "No se pudo generar el PDF del historial de cajas.", 500
    response = make_response(pdf_buffer.getvalue())
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f"attachment; filename=historial-cajas-{start.isoformat()}-{end.isoformat()}.pdf"
    return response


@dashboard_bp.route("/ventas/reporte/pdf")
@login_required
def sales_report_pdf():
    if role_code() not in {"ADMIN_GENERAL_NEGOCIO", "ADMIN_TIENDA"}:
        return redirect(url_for("dashboard.index"))
    period, start, end = resolve_period(request.args)
    sucursal_id = int(request.args.get("sucursal_id") or 0) or None
    cajero_id = int(request.args.get("cajero_id") or 0) or None
    if role_code() == "ADMIN_TIENDA":
        sucursal_id = g.user.get("sucursal_id")
    with db_cursor() as cursor:
        rows = sales_report_rows(cursor, start, end, sucursal_id=sucursal_id, cajero_id=cajero_id)
        sucursales = list_dashboard_sucursales(cursor)
        cajeros = list_dashboard_cashiers(cursor)
    sucursal_name = next((s["nombre"] for s in sucursales if sucursal_id and int(s["id"]) == int(sucursal_id)), "Todas" if role_code() == "ADMIN_GENERAL_NEGOCIO" else (g.user.get("sucursal_nombre") or "Mi tienda"))
    cajero_name = next((c["nombre_visible"] for c in cajeros if cajero_id and int(c["id"]) == int(cajero_id)), "Todos")
    totals = {
        "subtotal": sum((r.get("subtotal") or 0) for r in rows),
        "descuento": sum((r.get("descuento_total") or 0) for r in rows),
        "total": sum((r.get("total") or 0) for r in rows),
    }
    html = render_template(
        "reports/sales_history_pdf.html",
        rows=rows,
        totals=totals,
        periodo=period,
        fecha_inicio=start,
        fecha_fin=end,
        generado_en=datetime.now(),
        generado_por=user_full_name(),
        cliente=g.user.get("cliente_nombre"),
        sucursal_name=sucursal_name,
        cajero_name=cajero_name,
    )
    pdf_buffer = BytesIO()
    status = pisa.CreatePDF(html, dest=pdf_buffer, encoding="UTF-8")
    if status.err:
        return "No se pudo generar el PDF del reporte de ventas.", 500
    response = make_response(pdf_buffer.getvalue())
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f"attachment; filename=reporte-ventas-{start.isoformat()}-{end.isoformat()}.pdf"
    return response
