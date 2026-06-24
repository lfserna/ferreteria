from datetime import date, datetime, timedelta
from io import BytesIO

from flask import g, jsonify, make_response, render_template, request
from xhtml2pdf import pisa

from app.database import db_cursor
from app.utils.security import login_required


def _parse_date(value):
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _date_range(periodo, fecha_inicio=None, fecha_fin=None):
    today = date.today()
    if periodo == "DIA":
        return today, today, "Hoy"
    if periodo == "SEMANA":
        return today - timedelta(days=6), today, "Ultimos 7 dias"
    if periodo == "MES":
        return today.replace(day=1), today, "Mes actual"
    if periodo == "TRES_MESES":
        return today - timedelta(days=90), today, "Ultimos 3 meses"
    start = _parse_date(fecha_inicio) or today
    end = _parse_date(fecha_fin) or start
    if end < start:
        start, end = end, start
    return start, end, "Rango personalizado"


def _int_list(values):
    result = []
    for value in values:
        try:
            if str(value).strip():
                result.append(int(value))
        except ValueError:
            continue
    return result


def _placeholders(values):
    return ",".join(["%s"] * len(values))


def _movement_condition(tipo):
    if tipo == "ENTRADA":
        return "HAVING entradas_periodo > 0"
    if tipo == "SALIDA":
        return "HAVING salidas_periodo > 0"
    return ""


def _report_rows(cliente_id, start, end, categoria_ids, ubicacion_ids, tipo_movimiento):
    filters = ["p.cliente_id=%s", "p.deleted_at IS NULL"]
    params = [cliente_id]
    if categoria_ids:
        filters.append(f"p.categoria_id IN ({_placeholders(categoria_ids)})")
        params.extend(categoria_ids)
    if ubicacion_ids:
        filters.append(f"u.id IN ({_placeholders(ubicacion_ids)})")
        params.extend(ubicacion_ids)
    where = " AND ".join(filters)
    having = _movement_condition(tipo_movimiento)
    params.extend([start, end, start, end, start, end, start, end])
    with db_cursor() as cursor:
        cursor.execute(
            f"""
            SELECT
                p.nombre AS producto,
                COALESCE(cat.nombre, '-') AS categoria,
                COALESCE(u.nombre, '-') AS ubicacion,
                COALESCE(u.tipo_ubicacion, '-') AS tipo_ubicacion,
                COALESCE(pr.precio_venta_estandar, 0) AS precio,
                COALESCE(i.cantidad_disponible, 0) AS stock_actual,
                COALESCE(SUM(CASE
                    WHEN im.tipo_movimiento IN ('ENTRADA','TRASPASO') AND im.ubicacion_destino_id = u.id AND DATE(im.created_at) BETWEEN %s AND %s THEN im.cantidad
                    WHEN im.tipo_movimiento = 'AJUSTE_POSITIVO' AND im.ubicacion_origen_id = u.id AND DATE(im.created_at) BETWEEN %s AND %s THEN im.cantidad
                    ELSE 0
                END), 0) AS entradas_periodo,
                COALESCE(SUM(CASE
                    WHEN im.tipo_movimiento IN ('SALIDA','VENTA','TRASPASO') AND im.ubicacion_origen_id = u.id AND DATE(im.created_at) BETWEEN %s AND %s THEN im.cantidad
                    WHEN im.tipo_movimiento = 'AJUSTE_NEGATIVO' AND im.ubicacion_origen_id = u.id AND DATE(im.created_at) BETWEEN %s AND %s THEN im.cantidad
                    ELSE 0
                END), 0) AS salidas_periodo,
                MAX(im.created_at) AS ultimo_movimiento
            FROM productos p
            LEFT JOIN categorias_producto cat ON cat.id=p.categoria_id
            LEFT JOIN inventarios i ON i.producto_id=p.id AND i.cliente_id=p.cliente_id
            LEFT JOIN ubicaciones_stock u ON u.id=i.ubicacion_stock_id
            LEFT JOIN producto_presentaciones pp ON pp.producto_id=p.id AND pp.tipo_presentacion='UNIDAD'
            LEFT JOIN producto_precios pr ON pr.id=(
                SELECT pr2.id FROM producto_precios pr2
                WHERE pr2.producto_presentacion_id=pp.id
                ORDER BY pr2.id DESC LIMIT 1
            )
            LEFT JOIN inventario_movimientos im ON im.cliente_id=p.cliente_id AND im.producto_id=p.id
            WHERE {where}
            GROUP BY p.id, cat.nombre, u.id, u.nombre, u.tipo_ubicacion, pr.precio_venta_estandar, i.cantidad_disponible
            {having}
            ORDER BY u.tipo_ubicacion, u.nombre, cat.nombre, p.nombre
            """,
            tuple(params),
        )
        return cursor.fetchall()


def _filter_names(cliente_id, categoria_ids, ubicacion_ids):
    result = {"categorias": [], "ubicaciones": []}
    with db_cursor() as cursor:
        if categoria_ids:
            cursor.execute(
                f"SELECT nombre FROM categorias_producto WHERE cliente_id=%s AND id IN ({_placeholders(categoria_ids)}) ORDER BY nombre",
                tuple([cliente_id] + categoria_ids),
            )
            result["categorias"] = [row["nombre"] for row in cursor.fetchall()]
        if ubicacion_ids:
            cursor.execute(
                f"SELECT nombre FROM ubicaciones_stock WHERE cliente_id=%s AND id IN ({_placeholders(ubicacion_ids)}) ORDER BY tipo_ubicacion,nombre",
                tuple([cliente_id] + ubicacion_ids),
            )
            result["ubicaciones"] = [row["nombre"] for row in cursor.fetchall()]
    return result


@login_required
def inventario_reporte_pdf():
    if g.user["rol_codigo"] != "ADMIN_GENERAL_NEGOCIO":
        return jsonify({"error": "Solo el administrador general puede generar este reporte."}), 403

    periodo = request.args.get("periodo") or "DIA"
    start, end, period_label = _date_range(periodo, request.args.get("fecha_inicio"), request.args.get("fecha_fin"))
    categoria_ids = _int_list(request.args.getlist("categoria_id"))
    ubicacion_ids = _int_list(request.args.getlist("ubicacion_id"))
    tipo_movimiento = (request.args.get("tipo_movimiento") or "AMBOS").upper()
    if tipo_movimiento not in {"ENTRADA", "SALIDA", "AMBOS"}:
        tipo_movimiento = "AMBOS"

    rows = _report_rows(g.user["cliente_id"], start, end, categoria_ids, ubicacion_ids, tipo_movimiento)
    filter_names = _filter_names(g.user["cliente_id"], categoria_ids, ubicacion_ids)
    totals = {
        "stock": sum(int(row.get("stock_actual") or 0) for row in rows),
        "entradas": sum(int(row.get("entradas_periodo") or 0) for row in rows),
        "salidas": sum(int(row.get("salidas_periodo") or 0) for row in rows),
    }
    totals["neto"] = totals["entradas"] - totals["salidas"]

    html = render_template(
        "inventory/report_pdf.html",
        rows=rows,
        totals=totals,
        fecha_inicio=start,
        fecha_fin=end,
        periodo=period_label,
        tipo_movimiento=tipo_movimiento,
        filter_names=filter_names,
        cliente=g.user.get("cliente_nombre") or "Ferreteria",
        generado_por=g.user.get("username") or "usuario",
        generado_en=datetime.now(),
    )
    pdf_buffer = BytesIO()
    status = pisa.CreatePDF(html, dest=pdf_buffer, encoding="UTF-8")
    if status.err:
        return "No se pudo generar el PDF del reporte.", 500
    filename = f"reporte-inventario-{start.isoformat()}-{end.isoformat()}.pdf"
    response = make_response(pdf_buffer.getvalue())
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response
