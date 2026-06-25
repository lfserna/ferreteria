from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO
import json

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
    if tipo == "TRASPASO":
        return "HAVING traspasos_periodo > 0"
    return ""


def _money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _allowed_location_ids_for_user(cliente_id):
    role = g.user.get("rol_codigo")
    if role == "ADMIN_GENERAL_NEGOCIO":
        return None
    if role != "ADMIN_TIENDA":
        return []
    sucursal_id = g.user.get("sucursal_id")
    if not sucursal_id:
        return []
    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id
            FROM ubicaciones_stock
            WHERE cliente_id=%s AND sucursal_id=%s
            ORDER BY tipo_ubicacion, nombre
            """,
            (cliente_id, sucursal_id),
        )
        return [row["id"] for row in cursor.fetchall()]


def _restricted_location_ids(cliente_id, requested_ids):
    allowed = _allowed_location_ids_for_user(cliente_id)
    if allowed is None:
        return requested_ids
    if requested_ids:
        filtered = [location_id for location_id in requested_ids if location_id in set(allowed)]
        return filtered or [-1]
    return allowed or [-1]


def _report_rows(cliente_id, start, end, categoria_ids, ubicacion_ids, tipo_movimiento):
    filters = ["p.cliente_id=%s", "p.deleted_at IS NULL"]
    filter_params = [cliente_id]
    if categoria_ids:
        filters.append(f"p.categoria_id IN ({_placeholders(categoria_ids)})")
        filter_params.extend(categoria_ids)
    if ubicacion_ids:
        filters.append(f"u.id IN ({_placeholders(ubicacion_ids)})")
        filter_params.extend(ubicacion_ids)
    where = " AND ".join(filters)
    having = _movement_condition(tipo_movimiento)
    date_params = [start, end, start, end, start, end, start, end, start, end, start, end]
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
                COALESCE(SUM(CASE
                    WHEN im.tipo_movimiento = 'TRASPASO' AND (im.ubicacion_origen_id = u.id OR im.ubicacion_destino_id = u.id) AND DATE(im.created_at) BETWEEN %s AND %s THEN im.cantidad
                    ELSE 0
                END), 0) AS traspasos_periodo,
                MAX(CASE
                    WHEN (im.ubicacion_origen_id = u.id OR im.ubicacion_destino_id = u.id) AND DATE(im.created_at) BETWEEN %s AND %s THEN im.created_at
                    ELSE NULL
                END) AS fecha_movimiento
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
            tuple(date_params + filter_params),
        )
        rows = cursor.fetchall()

    for row in rows:
        if tipo_movimiento == "ENTRADA":
            cantidad = int(row.get("entradas_periodo") or 0)
        elif tipo_movimiento == "SALIDA":
            cantidad = int(row.get("salidas_periodo") or 0)
        elif tipo_movimiento == "TRASPASO":
            cantidad = int(row.get("traspasos_periodo") or 0)
        else:
            cantidad = int(row.get("stock_actual") or 0)
        precio = _money(row.get("precio") or 0)
        row["cantidad_reporte"] = cantidad
        row["total_bs"] = _money(precio * Decimal(cantidad))
    return rows


def _filter_names(cliente_id, categoria_ids, ubicacion_ids):
    result = {"categorias": [], "ubicaciones": []}
    with db_cursor() as cursor:
        if categoria_ids:
            cursor.execute(
                f"SELECT nombre FROM categorias_producto WHERE cliente_id=%s AND id IN ({_placeholders(categoria_ids)}) ORDER BY nombre",
                tuple([cliente_id] + categoria_ids),
            )
            result["categorias"] = [row["nombre"] for row in cursor.fetchall()]
        if ubicacion_ids and ubicacion_ids != [-1]:
            cursor.execute(
                f"SELECT nombre FROM ubicaciones_stock WHERE cliente_id=%s AND id IN ({_placeholders(ubicacion_ids)}) ORDER BY tipo_ubicacion,nombre",
                tuple([cliente_id] + ubicacion_ids),
            )
            result["ubicaciones"] = [row["nombre"] for row in cursor.fetchall()]
    return result


def _report_header_context(cliente_id, ubicacion_ids):
    with db_cursor() as cursor:
        cursor.execute("SELECT nombre_comercial, direccion, telefono FROM clientes WHERE id=%s LIMIT 1", (cliente_id,))
        cliente = cursor.fetchone() or {}
        context = {
            "cliente": cliente.get("nombre_comercial") or "Ferreteria",
            "ubicacion": "Todas las ubicaciones",
            "tipo_ubicacion": "GENERAL",
            "direccion": cliente.get("direccion") or "-",
            "celular": cliente.get("telefono") or "-",
        }
        if len(ubicacion_ids) != 1 or ubicacion_ids == [-1]:
            if len(ubicacion_ids) > 1:
                context["ubicacion"] = f"{len(ubicacion_ids)} ubicaciones seleccionadas"
            return context
        cursor.execute(
            """
            SELECT u.nombre AS ubicacion, u.tipo_ubicacion,
                   s.nombre AS sucursal_nombre, s.direccion AS sucursal_direccion, s.telefono AS sucursal_telefono,
                   a.nombre AS almacen_nombre, a.direccion AS almacen_direccion
            FROM ubicaciones_stock u
            LEFT JOIN sucursales s ON s.id=u.sucursal_id
            LEFT JOIN almacenes a ON a.id=u.almacen_id
            WHERE u.cliente_id=%s AND u.id=%s
            LIMIT 1
            """,
            (cliente_id, ubicacion_ids[0]),
        )
        location = cursor.fetchone()
        if not location:
            return context
        if location.get("tipo_ubicacion") == "SUCURSAL":
            context["ubicacion"] = location.get("sucursal_nombre") or location.get("ubicacion") or "Sucursal"
            context["direccion"] = location.get("sucursal_direccion") or context["direccion"]
            context["celular"] = location.get("sucursal_telefono") or context["celular"]
        else:
            context["ubicacion"] = location.get("almacen_nombre") or location.get("ubicacion") or "Almacen"
            context["direccion"] = location.get("almacen_direccion") or context["direccion"]
            context["celular"] = location.get("sucursal_telefono") or context["celular"]
        context["tipo_ubicacion"] = location.get("tipo_ubicacion") or "GENERAL"
        return context


def _ensure_inventory_report_table(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS inventario_reportes_generados (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            cliente_id BIGINT UNSIGNED NOT NULL,
            usuario_id BIGINT UNSIGNED NOT NULL,
            numero_reporte VARCHAR(40) NULL UNIQUE,
            fecha_inicio DATE NOT NULL,
            fecha_fin DATE NOT NULL,
            tipo_movimiento VARCHAR(20) NOT NULL,
            filtros_json JSON NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_inv_reportes_cliente_fecha (cliente_id, created_at)
        ) ENGINE=InnoDB
        """
    )


def _create_report_number(cliente_id, usuario_id, start, end, tipo_movimiento, categoria_ids, ubicacion_ids):
    filtros = json.dumps({"categoria_ids": categoria_ids, "ubicacion_ids": ubicacion_ids}, ensure_ascii=False)
    with db_cursor(commit=True) as cursor:
        _ensure_inventory_report_table(cursor)
        cursor.execute(
            """
            INSERT INTO inventario_reportes_generados
                (cliente_id, usuario_id, fecha_inicio, fecha_fin, tipo_movimiento, filtros_json, created_at)
            VALUES (%s,%s,%s,%s,%s,%s,NOW())
            """,
            (cliente_id, usuario_id, start, end, tipo_movimiento, filtros),
        )
        report_id = cursor.lastrowid
        report_number = f"REP-{report_id:06d}"
        cursor.execute("UPDATE inventario_reportes_generados SET numero_reporte=%s WHERE id=%s", (report_number, report_id))
        return report_number


@login_required
def inventario_reporte_pdf():
    if g.user["rol_codigo"] not in {"ADMIN_GENERAL_NEGOCIO", "ADMIN_TIENDA"}:
        return jsonify({"error": "Tu rol no puede generar este reporte."}), 403

    periodo = request.args.get("periodo") or "DIA"
    start, end, period_label = _date_range(periodo, request.args.get("fecha_inicio"), request.args.get("fecha_fin"))
    categoria_ids = _int_list(request.args.getlist("categoria_id"))
    ubicacion_ids = _restricted_location_ids(g.user["cliente_id"], _int_list(request.args.getlist("ubicacion_id")))
    tipo_movimiento = (request.args.get("tipo_movimiento") or "AMBOS").upper()
    if tipo_movimiento not in {"ENTRADA", "SALIDA", "TRASPASO", "AMBOS"}:
        tipo_movimiento = "AMBOS"

    rows = _report_rows(g.user["cliente_id"], start, end, categoria_ids, ubicacion_ids, tipo_movimiento)
    filter_names = _filter_names(g.user["cliente_id"], categoria_ids, ubicacion_ids)
    header_context = _report_header_context(g.user["cliente_id"], ubicacion_ids)
    total_cantidad = sum(int(row.get("cantidad_reporte") or 0) for row in rows)
    total_bs = sum((_money(row.get("total_bs") or 0) for row in rows), Decimal("0.00"))
    totals = {
        "cantidad": total_cantidad,
        "bs": _money(total_bs),
        "stock": sum(int(row.get("stock_actual") or 0) for row in rows),
        "entradas": sum(int(row.get("entradas_periodo") or 0) for row in rows),
        "salidas": sum(int(row.get("salidas_periodo") or 0) for row in rows),
        "traspasos": sum(int(row.get("traspasos_periodo") or 0) for row in rows),
    }
    generated_at = datetime.now()
    generated_by = " ".join(
        part for part in [g.user.get("nombres"), g.user.get("apellido_paterno"), g.user.get("apellido_materno")] if part
    ) or g.user.get("username") or "usuario"
    report_number = _create_report_number(g.user["cliente_id"], g.user["id"], start, end, tipo_movimiento, categoria_ids, ubicacion_ids)

    html = render_template(
        "inventory/report_pdf.html",
        rows=rows,
        totals=totals,
        fecha_inicio=start,
        fecha_fin=end,
        periodo=period_label,
        tipo_movimiento=tipo_movimiento,
        filter_names=filter_names,
        header_context=header_context,
        generado_por=generated_by,
        generado_en=generated_at,
        report_number=report_number,
    )
    pdf_buffer = BytesIO()
    status = pisa.CreatePDF(html, dest=pdf_buffer, encoding="UTF-8")
    if status.err:
        return "No se pudo generar el PDF del reporte.", 500
    filename = f"reporte-inventario-{report_number}.pdf"
    response = make_response(pdf_buffer.getvalue())
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response
