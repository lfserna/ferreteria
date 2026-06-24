from flask import Blueprint, g, render_template

from app.database import db_cursor
from app.utils.security import login_required


dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
@login_required
def index():
    cliente_id = g.user["cliente_id"]
    with db_cursor() as cursor:
        cursor.execute("SELECT COUNT(*) AS total FROM productos WHERE cliente_id = %s AND estado = 'ACTIVO'", (cliente_id,))
        productos = cursor.fetchone()["total"]
        cursor.execute("SELECT COALESCE(SUM(total),0) AS total FROM ventas WHERE cliente_id = %s AND DATE(fecha_venta)=CURDATE()", (cliente_id,))
        ventas_hoy = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) AS total FROM ventas WHERE cliente_id = %s AND DATE(fecha_venta)=CURDATE()", (cliente_id,))
        transacciones_hoy = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) AS total FROM inventarios WHERE cliente_id = %s AND cantidad_disponible <= cantidad_minima", (cliente_id,))
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
        cursor.execute("""
            SELECT v.id, v.numero_venta, v.fecha_venta, v.total, s.nombre AS sucursal, u.username AS cajero
            FROM ventas v
            LEFT JOIN sucursales s ON s.id = v.sucursal_id
            LEFT JOIN usuarios u ON u.id = v.cajero_id
            WHERE v.cliente_id = %s
            ORDER BY v.fecha_venta DESC
            LIMIT 8
        """, (cliente_id,))
        ultimas_ventas = cursor.fetchall()
    return render_template("dashboard.html", productos=productos, ventas_hoy=ventas_hoy,
                           transacciones_hoy=transacciones_hoy, stock_bajo=stock_bajo,
                           alertas=alertas, ultimas_ventas=ultimas_ventas)
