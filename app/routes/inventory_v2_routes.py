from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for
from mysql.connector.errors import IntegrityError

from app.database import db_cursor, db_transaction
from app.services.audit_service import log_audit
from app.services.category_admin_service import create_brand, create_category, estado_value, list_brands, list_categories_admin, update_brand, update_category
from app.services.context_service import get_primary_stock_location
from app.services.product_admin_service import barcode_aliases, create_product, parse_codes
from app.utils.permissions import can_manage_inventory, can_manage_products
from app.utils.security import login_required
from app.utils.serializers import to_jsonable

inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventario")


def wants_json():
    return request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.accept_mimetypes.best == "application/json"


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


def default_inventory_location_id():
    role = g.user.get("rol_codigo")
    if role in {"ADMIN_TIENDA", "VENDEDOR", "CAJERO", "ADMIN_ALMACEN"}:
        return get_primary_stock_location(g.user["cliente_id"], g.user.get("sucursal_id"), g.user.get("almacen_id"))
    return None


def products():
    with db_cursor() as c:
        c.execute("SELECT id,nombre,codigo_producto FROM productos WHERE cliente_id=%s AND estado='ACTIVO' ORDER BY nombre", (g.user["cliente_id"],))
        rows = c.fetchall()
        if rows:
            return rows
        c.execute("SELECT id,nombre,codigo_producto FROM productos WHERE cliente_id=%s ORDER BY nombre", (g.user["cliente_id"],))
        return c.fetchall()


def inventory_rows(query="", location_id=None, category_id=None):
    search = f"%{query.strip()}%"
    location_filter = ""
    category_filter = ""
    params = []
    if location_id:
        location_filter = " AND i.ubicacion_stock_id = %s"
        params.append(location_id)
    params.append(g.user["cliente_id"])
    if category_id:
        category_filter = " AND p.categoria_id = %s"
        params.append(category_id)
    params.extend([search, search, search, search])
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
              {category_filter}
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


def duplicated_integrity_message(error):
    text = str(error)
    lowered = text.lower()
    if "codigo_producto" in lowered:
        return "No se pudo guardar: el código general del producto ya existe. Déjalo vacío o usa otro."
    if "codigo_barras" in lowered:
        return "No se pudo guardar: el código de barras general del producto ya existe. Déjalo vacío o usa otro."
    if "producto_codigos" in lowered:
        return "No se pudo guardar por un código individual duplicado. Detalle técnico: " + text
    return "No se pudo guardar por un dato duplicado. Detalle técnico: " + text


@inventory_bp.route("")
@login_required
def index():
    managed_locations = locations(True)
    client_locations = locations(False)
    if can_manage_inventory() and not managed_locations:
        flash("No hay ubicaciones de inventario asignadas a tu rol. Revisa sucursal/almacén del usuario o carga ubicaciones_stock.", "warning")
    q = request.args.get("q", "")
    location_id = request.args.get("ubicacion_id") if "ubicacion_id" in request.args else default_inventory_location_id()
    location_id = location_id or None
    category_id = request.args.get("categoria_id") or None
    categorias = list_categories_admin(g.user["cliente_id"])
    return render_template("inventory/index.html", rows=inventory_rows(q, location_id, category_id), can_manage=can_manage_inventory(), can_edit_catalog=can_manage_products(), productos=products(), origenes=managed_locations, destinos=client_locations, ubicaciones=client_locations, categorias=categorias, marcas=list_brands(g.user["cliente_id"]), movimientos=movements(), q=q, ubicacion_id=location_id, categoria_id=category_id)


@inventory_bp.route("/buscar")
@login_required
def buscar():
    return jsonify(to_jsonable(inventory_rows(request.args.get("q", ""), request.args.get("ubicacion_id") or None, request.args.get("categoria_id") or None)))


@inventory_bp.route("/categorias/crear", methods=["POST"])
@login_required
def crear_categoria():
    if not can_manage_products():
        flash("No tienes permisos para crear categorías.", "danger")
        return redirect(url_for("inventory.index"))
    try:
        create_category(g.user["cliente_id"], g.user["id"], request.form)
        flash("Categoría creada correctamente.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("inventory.index"))


@inventory_bp.route("/categorias/<int:category_id>/editar", methods=["POST"])
@login_required
def editar_categoria(category_id):
    if not can_manage_products():
        flash("No tienes permisos para editar categorías.", "danger")
        return redirect(url_for("inventory.index"))
    try:
        update_category(g.user["cliente_id"], g.user["id"], category_id, request.form)
        flash("Categoría actualizada correctamente.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("inventory.index"))


@inventory_bp.route("/marcas/crear", methods=["POST"])
@login_required
def crear_marca():
    if not can_manage_products():
        flash("No tienes permisos para crear marcas.", "danger")
        return redirect(url_for("inventory.index"))
    try:
        create_brand(g.user["cliente_id"], g.user["id"], request.form)
        flash("Marca creada correctamente.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("inventory.index"))


@inventory_bp.route("/marcas/<int:brand_id>/editar", methods=["POST"])
@login_required
def editar_marca(brand_id):
    if not can_manage_products():
        flash("No tienes permisos para editar marcas.", "danger")
        return redirect(url_for("inventory.index"))
    try:
        update_brand(g.user["cliente_id"], g.user["id"], brand_id, request.form)
        flash("Marca actualizada correctamente.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("inventory.index"))


@inventory_bp.route("/productos/crear", methods=["POST"])
@login_required
def crear_producto():
    if not can_manage_products():
        message = "No tienes permisos para crear productos."
        if wants_json():
            return jsonify({"ok": False, "message": message}), 403
        flash(message, "danger")
        return redirect(url_for("inventory.index"))
    try:
        result = create_product(g.user["cliente_id"], g.user["id"], request.form)
        skipped = result.get("codes_skipped") if isinstance(result, dict) else 0
        if wants_json():
            return jsonify({"ok": True, "message": "Producto creado correctamente.", "product_id": result.get("product_id") if isinstance(result, dict) else result, "codes_skipped": skipped})
        message = "Producto creado correctamente."
        if skipped:
            message += f" Se omitieron {skipped} códigos individuales ya existentes."
        flash(message, "success")
    except IntegrityError as e:
        message = duplicated_integrity_message(e)
        if wants_json():
            return jsonify({"ok": False, "message": message}), 400
        flash(message, "danger")
    except ValueError as e:
        if wants_json():
            return jsonify({"ok": False, "message": str(e)}), 400
        flash(str(e), "danger")
    return redirect(url_for("inventory.index"))
