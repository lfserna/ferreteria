from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for

from app.database import db_cursor, db_transaction
from app.services.audit_service import log_audit
from app.services.product_service import list_stock
from app.utils.permissions import can_manage_inventory
from app.utils.security import login_required
from app.utils.serializers import to_jsonable


inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventario")


def int_qty(value, label="cantidad"):
    try:
        number = int(value)
    except Exception as exc:
        raise ValueError(f"La {label} debe ser un número entero.") from exc
    if number <= 0:
        raise ValueError(f"La {label} debe ser mayor a cero.")
    return number


def origin_locations():
    params = [g.user["cliente_id"]]
    where = "cliente_id=%s AND estado='ACTIVO'"
    role = g.user["rol_codigo"]
    if role == "ADMIN_TIENDA":
        where += " AND tipo_ubicacion='SUCURSAL' AND sucursal_id=%s"
        params.append(g.user.get("sucursal_id"))
    elif role == "ADMIN_ALMACEN":
        where += " AND tipo_ubicacion='ALMACEN' AND almacen_id=%s"
        params.append(g.user.get("almacen_id"))
    elif role != "ADMIN_GENERAL_NEGOCIO":
        where += " AND 1=0"
    with db_cursor() as cursor:
        cursor.execute(f"SELECT id,nombre,tipo_ubicacion FROM ubicaciones_stock WHERE {where} ORDER BY tipo_ubicacion,nombre", tuple(params))
        return cursor.fetchall()


def all_locations():
    with db_cursor() as cursor:
        cursor.execute("SELECT id,nombre,tipo_ubicacion FROM ubicaciones_stock WHERE cliente_id=%s AND estado='ACTIVO' ORDER BY tipo_ubicacion,nombre", (g.user["cliente_id"],))
        return cursor.fetchall()


def products_active():
    with db_cursor() as cursor:
        cursor.execute("SELECT id,nombre,codigo_producto FROM productos WHERE cliente_id=%s AND estado='ACTIVO' ORDER BY nombre", (g.user["cliente_id"],))
        return cursor.fetchall()


def assert_allowed_origin(location_id):
    allowed = {int(row["id"]) for row in origin_locations()}
    if int(location_id) not in allowed:
        raise ValueError("No puedes gestionar inventario en esa ubicación.")


def ensure_inventory(cursor, producto_id, ubicacion_id):
    cursor.execute("SELECT id FROM inventarios WHERE cliente_id=%s AND producto_id=%s AND ubicacion_stock_id=%s LIMIT 1", (g.user["cliente_id"], producto_id, ubicacion_id))
    row = cursor.fetchone()
    if row:
        return row["id"]
    cursor.execute("INSERT INTO inventarios (cliente_id,producto_id,ubicacion_stock_id,cantidad_disponible,cantidad_reservada,cantidad_minima,updated_at) VALUES (%s,%s,%s,0,0,0,NOW())", (g.user["cliente_id"], producto_id, ubicacion_id))
    return cursor.lastrowid


def latest_movements():
    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT im.id, im.tipo_movimiento, im.cantidad, im.created_at, im.observacion,
                   p.nombre AS producto, uo.nombre AS origen, ud.nombre AS destino, usr.username AS usuario
            FROM inventario_movimientos im
            JOIN productos p ON p.id=im.producto_id
            LEFT JOIN ubicaciones_stock uo ON uo.id=im.ubicacion_origen_id
            LEFT JOIN ubicaciones_stock ud ON ud.id=im.ubicacion_destino_id
            LEFT JOIN usuarios usr ON usr.id=im.usuario_id
            WHERE im.cliente_id=%s
            ORDER BY im.created_at DESC, im.id DESC
            LIMIT 60
            """,
            (g.user["cliente_id"],),
        )
        return cursor.fetchall()


@inventory_bp.route("")
@login_required
def index():
    rows = list_stock(g.user["cliente_id"], request.args.get("q", ""))
    return render_template("inventory/index.html", rows=rows, can_manage=can_manage_inventory(), productos=products_active(), origenes=origin_locations(), destinos=all_locations(), movimientos=latest_movements())


@inventory_bp.route("/buscar")
@login_required
def buscar():
    return jsonify(to_jsonable(list_stock(g.user["cliente_id"], request.args.get("q", ""))))


@inventory_bp.route("/ajustar", methods=["POST"])
@login_required
def ajustar():
    if not can_manage_inventory():
        flash("No tienes permisos para ajustar inventario.", "danger")
        return redirect(url_for("inventory.index"))
    try:
        producto_id = int(request.form.get("producto_id") or 0)
        ubicacion_id = int(request.form.get("ubicacion_stock_id") or 0)
        cantidad = int_qty(request.form.get("cantidad_disponible"), "cantidad disponible")
        minimo = int(request.form.get("cantidad_minima") or 0)
        assert_allowed_origin(ubicacion_id)
        with db_transaction() as (cursor, _connection):
            inv_id = ensure_inventory(cursor, producto_id, ubicacion_id)
            cursor.execute("SELECT * FROM inventarios WHERE id=%s FOR UPDATE", (inv_id,))
            previous = cursor.fetchone()
            diff = cantidad - int(previous["cantidad_disponible"])
            cursor.execute("UPDATE inventarios SET cantidad_disponible=%s,cantidad_minima=%s,updated_at=NOW() WHERE id=%s", (cantidad, minimo, inv_id))
            tipo = "AJUSTE_POSITIVO" if diff >= 0 else "AJUSTE_NEGATIVO"
            cursor.execute("INSERT INTO inventario_movimientos (cliente_id,producto_id,ubicacion_origen_id,tipo_movimiento,cantidad,referencia_tipo,referencia_id,usuario_id,observacion,created_at) VALUES (%s,%s,%s,%s,%s,'INVENTARIO',%s,%s,%s,NOW())", (g.user["cliente_id"], producto_id, ubicacion_id, tipo, abs(diff), inv_id, g.user["id"], request.form.get("observacion") or "Ajuste manual"))
            log_audit(cursor, cliente_id=g.user["cliente_id"], usuario_id=g.user["id"], modulo="INVENTARIO", accion="AJUSTAR_STOCK", tabla_afectada="inventarios", registro_id=inv_id, valor_anterior=previous, valor_nuevo={"cantidad_disponible": cantidad, "cantidad_minima": minimo})
        flash("Inventario ajustado correctamente.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("inventory.index"))


@inventory_bp.route("/movimiento", methods=["POST"])
@login_required
def movimiento():
    if not can_manage_inventory():
        flash("No tienes permisos para crear movimientos.", "danger")
        return redirect(url_for("inventory.index"))
    try:
        producto_id = int(request.form.get("producto_id") or 0)
        tipo = request.form.get("tipo_movimiento") or "ENTRADA"
        cantidad = int_qty(request.form.get("cantidad"))
        origen_id = int(request.form.get("ubicacion_origen_id") or 0) or None
        destino_id = int(request.form.get("ubicacion_destino_id") or 0) or None
        if tipo in {"SALIDA", "TRASPASO"}:
            if not origen_id:
                raise ValueError("Selecciona origen.")
            assert_allowed_origin(origen_id)
        if tipo in {"ENTRADA", "TRASPASO"} and not destino_id:
            raise ValueError("Selecciona destino.")
        with db_transaction() as (cursor, _connection):
            if tipo in {"SALIDA", "TRASPASO"}:
                inv_id = ensure_inventory(cursor, producto_id, origen_id)
                cursor.execute("SELECT * FROM inventarios WHERE id=%s FOR UPDATE", (inv_id,))
                inv = cursor.fetchone()
                if int(inv["cantidad_disponible"]) < cantidad:
                    raise ValueError("Stock insuficiente en origen.")
                cursor.execute("UPDATE inventarios SET cantidad_disponible=cantidad_disponible-%s,updated_at=NOW() WHERE id=%s", (cantidad, inv_id))
            if tipo in {"ENTRADA", "TRASPASO"}:
                inv_id = ensure_inventory(cursor, producto_id, destino_id)
                cursor.execute("SELECT id FROM inventarios WHERE id=%s FOR UPDATE", (inv_id,))
                cursor.execute("UPDATE inventarios SET cantidad_disponible=cantidad_disponible+%s,updated_at=NOW() WHERE id=%s", (cantidad, inv_id))
            cursor.execute("INSERT INTO inventario_movimientos (cliente_id,producto_id,ubicacion_origen_id,ubicacion_destino_id,tipo_movimiento,cantidad,referencia_tipo,usuario_id,observacion,created_at) VALUES (%s,%s,%s,%s,%s,%s,'MANUAL',%s,%s,NOW())", (g.user["cliente_id"], producto_id, origen_id, destino_id, tipo, cantidad, g.user["id"], request.form.get("observacion") or None))
            mov_id = cursor.lastrowid
            log_audit(cursor, cliente_id=g.user["cliente_id"], usuario_id=g.user["id"], modulo="INVENTARIO", accion=f"MOVIMIENTO_{tipo}", tabla_afectada="inventario_movimientos", registro_id=mov_id, valor_nuevo={"producto_id": producto_id, "cantidad": cantidad, "origen_id": origen_id, "destino_id": destino_id})
        flash("Movimiento registrado correctamente.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("inventory.index"))
