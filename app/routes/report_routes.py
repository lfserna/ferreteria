from flask import Blueprint, g, render_template, request

from app.database import db_cursor
from app.utils.security import login_required


reports_bp = Blueprint("reports", __name__, url_prefix="/reportes")


@reports_bp.route("")
@login_required
def index():
    cliente_id = g.user["cliente_id"]
    fecha_inicio = request.args.get("fecha_inicio", "")
    fecha_fin = request.args.get("fecha_fin", "")
    params = [cliente_id]
    where = "WHERE v.cliente_id = %s"
    if fecha_inicio:
        where += " AND DATE(v.fecha_venta) >= %s"
        params.append(fecha_inicio)
    if fecha_fin:
        where += " AND DATE(v.fecha_venta) <= %s"
        params.append(fecha_fin)
    with db_cursor() as cursor:
        cursor.execute(f"""
            SELECT s.nombre AS sucursal, COUNT(v.id) AS ventas, COALESCE(SUM(v.total),0) AS total
            FROM ventas v
            LEFT JOIN sucursales s ON s.id = v.sucursal_id
            {where}
            GROUP BY s.nombre
            ORDER BY total DESC
        """, tuple(params))
        por_sucursal = cursor.fetchall()
        cursor.execute(f"""
            SELECT p.nombre AS producto, SUM(vd.cantidad) AS cantidad, SUM(vd.subtotal) AS total
            FROM venta_detalles vd
            JOIN ventas v ON v.id = vd.venta_id
            JOIN productos p ON p.id = vd.producto_id
            {where}
            GROUP BY p.nombre
            ORDER BY total DESC
            LIMIT 20
        """, tuple(params))
        por_producto = cursor.fetchall()
    return render_template("reports/index.html", por_sucursal=por_sucursal, por_producto=por_producto,
                           fecha_inicio=fecha_inicio, fecha_fin=fecha_fin)
