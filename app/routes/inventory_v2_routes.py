from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for
from mysql.connector.errors import IntegrityError

from app.database import db_cursor, db_transaction
from app.services.audit_service import log_audit
from app.services.category_admin_service import estado_value, list_brands, list_categories_admin
from app.services.product_admin_service import create_product
from app.utils.permissions import can_manage_inventory, can_manage_products
from app.utils.security import login_required
from app.utils.serializers import to_jsonable

inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventario")


def qty(v):
    n = int(v or 0)
    if n <= 0:
        raise ValueError("La cantidad debe ser un número entero mayor a cero.")
    return n


def location_where(managed=False, strict_active=True):
    sql = "SELECT id,nombre,tipo_ubicacion FROM ubicaciones_stock WHERE cliente_id=%s"
    params = [g.user["cliente_id"]]
    if strict_active:
        sql += " AND estado='ACTIVO'"
    if managed:
        role = g.user["rol_codigo"]
        if role == "ADMIN_TIENDA":
            sql += " AND tipo_ubicacion='SUCURSAL' AND sucursal_id=%s"
            params.append(g.user.get("sucursal_id"))
        elif role == "ADMIN_ALMACEN":
            sql += " AND tipo_ubicacion='ALMACEN' AND almacen_id=%s"
            params.append(g.user.get("almacen_id"))
        elif role != "ADMIN_GENERAL_NEGOCIO":
            sql += " AND 1=0"
    return sql + " ORDER BY tipo_ubicacion,nombre", tuple(params)


def locations(managed=False):
    with db_cursor() as c:
        sql, params = location_where(managed=managed, strict_active=True)
        c.execute(sql, params)
        rows = c.fetchall()
        if rows:
            return rows
        sql, params = location_where(managed=managed, strict_active=False)
        c.execute(sql, params)
        return c.fetchall()


def products():
    with db_cursor() as c:
        c.execute("SELECT id,nombre,codigo_producto FROM productos WHERE cliente_id=%s AND estado='ACTIVO' ORDER BY nombre", (g.user["cliente_id"],))
        rows = c.fetchall()
        if rows:
            return rows
        c.execute("SELECT id,nombre,codigo_producto FROM productos WHERE cliente_id=%s ORDER BY nombre", (g.user["cliente_id"],))
        return c.fetchall()


def inventory_rows(query="", location_id=None):
    search = f"%{query.strip()}%"
    location_filter = ""
    params = []
    if location_id:
        location_filter = " AND i.ubicacion_stock_id = %s"
        params.append(location_id)
    params.extend([g.user["cliente_id"], search, search, search, search])
    with db_cursor() as c:
        c.execute(f"""
            SELECT p.id AS producto_id, p.nombre, p.descripcion, p.codigo_producto, p.codigo_barras,
                   cat.nombre AS categoria, COALESCE(pr.precio_venta_estandar, 0) AS precio,
                   COALESCE(SUM(i.cantidad_disponible), 0) AS stock_total
            FROM productos p
            LEFT JOIN categorias_producto cat ON cat.id=p.categoria_id
            LEFT JOIN producto_presentaciones pp ON pp.producto_id=p.id AND pp.tipo_presentacion='UNIDAD'
            LEFT JOIN producto_precios pr ON pr.id=(
                SELECT pr2.id FROM producto_precios pr2
                WHERE pr2.producto_presentacion_id=pp.id
                ORDER BY pr2.id DESC LIMIT 1
            )
            LEFT JOIN inventarios i ON i.producto_id=p.id AND i.cliente_id=p.cliente_id {location_filter}
            WHERE p.cliente_id=%s
              AND p.deleted_at IS NULL
              AND (p.nombre LIKE %s OR COALESCE(p.descripcion,'') LIKE %s OR COALESCE(p.codigo_producto,'') LIKE %s OR COALESCE(p.codigo_barras,'') LIKE %s)
            GROUP BY p.id, cat.nombre, pr.precio_venta_estandar
            ORDER BY p.nombre
            LIMIT 300
        """, tuple(params))
        return c.fetchall()


def movements(product_id=None):
    params = [g.user["cliente_id"]]
    extra = ""
    if product_id:
        extra = " AND im.producto_id=%s"
        params.append(product_id)
    with db_cursor() as c:
        c.execute(f"""
            SELECT im.id,im.tipo_movimiento,im.cantidad,im.created_at,im.observacion,
                   p.nombre AS producto, uo.nombre AS origen, ud.nombre AS destino, usr.username AS usuario
            FROM inventario_movimientos im
            JOIN productos p ON p.id=im.producto_id
            LEFT JOIN ubicaciones_stock uo ON uo.id=im.ubicacion_origen_id
            LEFT JOIN ubicaciones_stock ud ON ud.id=im.ubicacion_destino_id
            LEFT JOIN usuarios usr ON usr.id=im.usuario_id
            WHERE im.cliente_id=%s {extra}
            ORDER BY im.created_at DESC, im.id DESC LIMIT 60
        """, tuple(params))
        return c.fetchall()


def allowed_managed(location_id):
    ids = {int(x["id"]) for x in locations(True)}
    if int(location_id) not in ids:
        raise ValueError("No puedes gestionar inventario en esa ubicación.")


def allowed_client(location_id):
    ids = {int(x["id"]) for x in locations(False)}
    if int(location_id) not in ids:
        raise ValueError("La ubicación no pertenece al cliente.")


def inv_id(c, product_id, location_id):
    c.execute("SELECT id FROM inventarios WHERE cliente_id=%s AND producto_id=%s AND ubicacion_stock_id=%s LIMIT 1", (g.user["cliente_id"], product_id, location_id))
    row = c.fetchone()
    if row:
        return row["id"]
    c.execute("INSERT INTO inventarios (cliente_id,producto_id,ubicacion_stock_id,cantidad_disponible,cantidad_reservada,cantidad_minima,updated_at) VALUES (%s,%s,%s,0,0,0,NOW())", (g.user["cliente_id"], product_id, location_id))
    return c.lastrowid


@inventory_bp.route("")
@login_required
def index():
    managed_locations = locations(True)
    client_locations = locations(False)
    if can_manage_inventory() and not managed_locations:
        flash("No hay ubicaciones de inventario asignadas a tu rol. Revisa sucursal/almacén del usuario o carga ubicaciones_stock.", "warning")
    q = request.args.get("q", "")
    location_id = request.args.get("ubicacion_id") or None
    return render_template(
        "inventory/index.html",
        rows=inventory_rows(q, location_id),
        can_manage=can_manage_inventory(),
        can_edit_catalog=can_manage_products(),
        productos=products(),
        origenes=managed_locations,
        destinos=client_locations,
        ubicaciones=client_locations,
        categorias=list_categories_admin(g.user["cliente_id"]),
        marcas=list_brands(g.user["cliente_id"]),
        movimientos=movements(),
        q=q,
        ubicacion_id=location_id,
    )


@inventory_bp.route("/buscar")
@login_required
def buscar():
    return jsonify(to_jsonable(inventory_rows(request.args.get("q", ""), request.args.get("ubicacion_id") or None)))


@inventory_bp.route("/productos/crear", methods=["POST"])
@login_required
def crear_producto():
    if not can_manage_products():
        flash("No tienes permisos para crear productos.", "danger")
        return redirect(url_for("inventory.index"))
    try:
        cantidad = qty(request.form.get("cantidad_inicial"))
        ubicacion_id = int(request.form.get("ubicacion_stock_id") or 0)
        allowed_managed(ubicacion_id)
        data = request.form.copy()
        data["cantidad_inicial"] = str(cantidad)
        create_product(g.user["cliente_id"], g.user["id"], data)
        flash("Producto agregado correctamente al inventario.", "success")
    except IntegrityError:
        flash("No se pudo guardar: uno de los códigos individuales ya existe.", "danger")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("inventory.index"))


@inventory_bp.route("/api/productos/<int:product_id>")
@login_required
def detalle_producto(product_id):
    with db_cursor() as c:
        c.execute("""
            SELECT p.id,p.nombre,p.descripcion,p.codigo_producto,p.codigo_barras,cat.nombre AS categoria
            FROM productos p LEFT JOIN categorias_producto cat ON cat.id=p.categoria_id
            WHERE p.cliente_id=%s AND p.id=%s LIMIT 1
        """, (g.user["cliente_id"], product_id))
        product = c.fetchone()
        if not product:
            return jsonify({"error": "Producto no encontrado"}), 404
        c.execute("""
            SELECT pp.id AS presentacion_id, pp.nombre AS presentacion, pp.tipo_presentacion,
                   COALESCE(pr.precio_venta_estandar,0) AS precio,
                   COALESCE(pr.precio_minimo_venta,0) AS minimo
            FROM producto_presentaciones pp
            LEFT JOIN producto_precios pr ON pr.id=(
                SELECT pr2.id FROM producto_precios pr2
                WHERE pr2.producto_presentacion_id=pp.id
                ORDER BY pr2.id DESC LIMIT 1
            )
            WHERE pp.cliente_id=%s AND pp.producto_id=%s
            ORDER BY CASE WHEN pp.tipo_presentacion='UNIDAD' THEN 0 ELSE 1 END, pp.factor_unidad_base
        """, (g.user["cliente_id"], product_id))
        product["presentaciones"] = c.fetchall()
        c.execute("""
            SELECT u.nombre AS ubicacion, u.tipo_ubicacion, COALESCE(i.cantidad_disponible,0) AS stock
            FROM ubicaciones_stock u
            LEFT JOIN inventarios i ON i.ubicacion_stock_id=u.id AND i.producto_id=%s AND i.cliente_id=u.cliente_id
            WHERE u.cliente_id=%s
            ORDER BY u.tipo_ubicacion,u.nombre
        """, (product_id, g.user["cliente_id"]))
        product["stock_ubicaciones"] = c.fetchall()
        return jsonify(to_jsonable(product))


@inventory_bp.route("/presentacion-precio", methods=["POST"])
@login_required
def presentacion_precio():
    if not can_manage_products():
        flash("No tienes permisos para editar presentación ni precios.", "danger")
        return redirect(url_for("inventory.index"))
    try:
        product_id = int(request.form.get("producto_id") or 0)
        presentacion = (request.form.get("presentacion") or "Unidad").strip() or "Unidad"
        precio = float(request.form.get("precio_venta_estandar") or 0)
        minimo = float(request.form.get("precio_minimo_venta") or 0)
        if precio <= 0:
            raise ValueError("El precio debe ser mayor a cero.")
        if minimo < 0 or minimo > precio:
            raise ValueError("El precio mínimo debe ser mayor o igual a cero y no puede superar el precio.")
        with db_transaction() as (c, _):
            presentacion_activa = estado_value(c, "producto_presentaciones", "ACTIVO")
            precio_activo = estado_value(c, "producto_precios", "ACTIVO")
            precio_inactivo = estado_value(c, "producto_precios", "INACTIVO")
            c.execute("SELECT id,nombre FROM productos WHERE cliente_id=%s AND id=%s FOR UPDATE", (g.user["cliente_id"], product_id))
            product = c.fetchone()
            if not product:
                raise ValueError("Producto no encontrado.")
            c.execute("SELECT * FROM producto_presentaciones WHERE cliente_id=%s AND producto_id=%s AND tipo_presentacion='UNIDAD' LIMIT 1", (g.user["cliente_id"], product_id))
            previous_presentation = c.fetchone()
            if previous_presentation:
                presentation_id = previous_presentation["id"]
                c.execute("UPDATE producto_presentaciones SET nombre=%s,factor_unidad_base=1,estado=%s,updated_at=NOW() WHERE id=%s", (presentacion, presentacion_activa, presentation_id))
            else:
                c.execute("INSERT INTO producto_presentaciones (cliente_id,producto_id,tipo_presentacion,nombre,factor_unidad_base,estado,created_at,updated_at) VALUES (%s,%s,'UNIDAD',%s,1,%s,NOW(),NOW())", (g.user["cliente_id"], product_id, presentacion, presentacion_activa))
                presentation_id = c.lastrowid
            c.execute("UPDATE producto_precios SET estado=%s,updated_at=NOW() WHERE cliente_id=%s AND producto_presentacion_id=%s", (precio_inactivo, g.user["cliente_id"], presentation_id))
            c.execute("INSERT INTO producto_precios (cliente_id,producto_id,producto_presentacion_id,precio_venta_estandar,precio_minimo_venta,moneda,vigente_desde,estado,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,'BOB',NOW(),%s,NOW(),NOW())", (g.user["cliente_id"], product_id, presentation_id, precio, minimo, precio_activo))
            log_audit(c, cliente_id=g.user["cliente_id"], usuario_id=g.user["id"], modulo="CATALOGO", accion="EDITAR_PRESENTACION_PRECIO", tabla_afectada="producto_precios", registro_id=presentation_id, valor_anterior=previous_presentation, valor_nuevo={"producto_id": product_id, "presentacion": presentacion, "precio": precio, "minimo": minimo})
        flash("Presentación y precio actualizados correctamente.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("inventory.index"))


@inventory_bp.route("/ajustar", methods=["POST"])
@login_required
def ajustar():
    if not can_manage_inventory():
        flash("No tienes permisos para ajustar inventario.", "danger")
        return redirect(url_for("inventory.index"))
    try:
        product_id = int(request.form.get("producto_id") or 0)
        location_id = int(request.form.get("ubicacion_stock_id") or 0)
        amount = qty(request.form.get("cantidad_disponible"))
        minimum = int(request.form.get("cantidad_minima") or 0)
        allowed_managed(location_id)
        with db_transaction() as (c, _):
            iid = inv_id(c, product_id, location_id)
            c.execute("SELECT * FROM inventarios WHERE id=%s FOR UPDATE", (iid,))
            old = c.fetchone()
            diff = amount - int(old["cantidad_disponible"])
            c.execute("UPDATE inventarios SET cantidad_disponible=%s,cantidad_minima=%s,updated_at=NOW() WHERE id=%s", (amount, minimum, iid))
            kind = "AJUSTE_POSITIVO" if diff >= 0 else "AJUSTE_NEGATIVO"
            c.execute("INSERT INTO inventario_movimientos (cliente_id,producto_id,ubicacion_origen_id,tipo_movimiento,cantidad,referencia_tipo,referencia_id,usuario_id,observacion,created_at) VALUES (%s,%s,%s,%s,%s,'INVENTARIO',%s,%s,%s,NOW())", (g.user["cliente_id"], product_id, location_id, kind, abs(diff), iid, g.user["id"], request.form.get("observacion") or "Ajuste manual"))
            log_audit(c, cliente_id=g.user["cliente_id"], usuario_id=g.user["id"], modulo="INVENTARIO", accion="AJUSTAR_STOCK", tabla_afectada="inventarios", registro_id=iid, valor_anterior=old, valor_nuevo={"cantidad_disponible": amount, "cantidad_minima": minimum})
        flash("Inventario ajustado correctamente.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("inventory.index"))


@inventory_bp.route("/movimiento", methods=["POST"])
@login_required
def movimiento():
    if not can_manage_inventory():
        flash("No tienes permisos para crear movimientos.", "danger")
        return redirect(url_for("inventory.index"))
    try:
        product_id = int(request.form.get("producto_id") or 0)
        kind = request.form.get("tipo_movimiento") or "ENTRADA"
        amount = qty(request.form.get("cantidad"))
        origin = int(request.form.get("ubicacion_origen_id") or 0) or None
        target = int(request.form.get("ubicacion_destino_id") or 0) or None
        if kind == "ENTRADA":
            if not target:
                raise ValueError("Selecciona la ubicación de destino.")
            allowed_managed(target)
            origin = None
        elif kind == "SALIDA":
            if not origin:
                raise ValueError("Selecciona la ubicación de origen.")
            allowed_managed(origin)
            target = None
        elif kind == "TRASPASO":
            if not origin or not target:
                raise ValueError("Selecciona origen y destino.")
            allowed_managed(origin)
            allowed_client(target)
            if origin == target:
                raise ValueError("Origen y destino no pueden ser iguales.")
        else:
            raise ValueError("Tipo de movimiento inválido.")
        with db_transaction() as (c, _):
            if origin:
                iid = inv_id(c, product_id, origin)
                c.execute("SELECT cantidad_disponible FROM inventarios WHERE id=%s FOR UPDATE", (iid,))
                row = c.fetchone()
                if int(row["cantidad_disponible"]) < amount:
                    raise ValueError("Stock insuficiente en origen.")
                c.execute("UPDATE inventarios SET cantidad_disponible=cantidad_disponible-%s,updated_at=NOW() WHERE id=%s", (amount, iid))
            if target:
                iid = inv_id(c, product_id, target)
                c.execute("SELECT id FROM inventarios WHERE id=%s FOR UPDATE", (iid,))
                c.fetchone()
                c.execute("UPDATE inventarios SET cantidad_disponible=cantidad_disponible+%s,updated_at=NOW() WHERE id=%s", (amount, iid))
            c.execute("INSERT INTO inventario_movimientos (cliente_id,producto_id,ubicacion_origen_id,ubicacion_destino_id,tipo_movimiento,cantidad,referencia_tipo,usuario_id,observacion,created_at) VALUES (%s,%s,%s,%s,%s,%s,'MANUAL',%s,%s,NOW())", (g.user["cliente_id"], product_id, origin, target, kind, amount, g.user["id"], request.form.get("observacion") or None))
            mid = c.lastrowid
            log_audit(c, cliente_id=g.user["cliente_id"], usuario_id=g.user["id"], modulo="INVENTARIO", accion=f"MOVIMIENTO_{kind}", tabla_afectada="inventario_movimientos", registro_id=mid, valor_nuevo={"producto_id": product_id, "cantidad": amount, "origen_id": origin, "destino_id": target})
        flash("Movimiento registrado correctamente.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("inventory.index"))
