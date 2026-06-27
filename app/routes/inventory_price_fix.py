from flask import g, jsonify, redirect, request, url_for, flash

from app.database import db_cursor, db_transaction
from app.services.audit_service import log_audit
from app.services.category_admin_service import estado_value
from app.services.price_schema_service import ensure_extra_price_columns, extra_prices_from_data
from app.utils.security import login_required
from app.utils.serializers import to_jsonable

PRICE_MANAGERS = {"ADMIN_GENERAL_NEGOCIO", "ADMIN_TIENDA"}


@login_required
def detalle_producto_fixed(product_id):
    with db_cursor(commit=True) as c:
        ensure_extra_price_columns(c)
        c.execute(
            """
            SELECT p.id,p.nombre,p.descripcion,p.codigo_producto,p.codigo_barras,cat.nombre AS categoria
            FROM productos p LEFT JOIN categorias_producto cat ON cat.id=p.categoria_id
            WHERE p.cliente_id=%s AND p.id=%s LIMIT 1
            """,
            (g.user["cliente_id"], product_id),
        )
        product = c.fetchone()
        if not product:
            return jsonify({"error": "Producto no encontrado"}), 404
        c.execute(
            """
            SELECT pp.id AS presentacion_id, pp.nombre AS presentacion, pp.tipo_presentacion,
                   COALESCE(pr.precio_venta_estandar,0) AS precio,
                   COALESCE(pr.precio_minimo_venta,0) AS minimo,
                   pr.precio_cuarta, pr.precio_media, pr.precio_docena, pr.precio_caja
            FROM producto_presentaciones pp
            LEFT JOIN producto_precios pr ON pr.id=(
                SELECT pr2.id FROM producto_precios pr2
                WHERE pr2.producto_presentacion_id=pp.id
                ORDER BY pr2.id DESC LIMIT 1
            )
            WHERE pp.cliente_id=%s AND pp.producto_id=%s
            ORDER BY CASE WHEN pp.tipo_presentacion='UNIDAD' THEN 0 ELSE 1 END, pp.factor_unidad_base
            """,
            (g.user["cliente_id"], product_id),
        )
        product["presentaciones"] = c.fetchall()
        c.execute(
            """
            SELECT u.nombre AS ubicacion, u.tipo_ubicacion, COALESCE(i.cantidad_disponible,0) AS stock
            FROM ubicaciones_stock u
            LEFT JOIN inventarios i ON i.ubicacion_stock_id=u.id AND i.producto_id=%s AND i.cliente_id=u.cliente_id
            WHERE u.cliente_id=%s
            ORDER BY u.tipo_ubicacion,u.nombre
            """,
            (product_id, g.user["cliente_id"]),
        )
        product["stock_ubicaciones"] = c.fetchall()
        return jsonify(to_jsonable(product))


@login_required
def presentacion_precio_fixed():
    if not g.user or g.user.get("rol_codigo") not in PRICE_MANAGERS:
        flash("No tienes permisos para editar precios.", "danger")
        return redirect(url_for("inventory.index"))
    try:
        product_id = int(request.form.get("producto_id") or 0)
        presentacion = (request.form.get("presentacion") or "Unidad").strip() or "Unidad"
        precio = float(request.form.get("precio_venta_estandar") or 0)
        minimo = float(request.form.get("precio_minimo_venta") or 0)
        if precio <= 0:
            raise ValueError("El precio unitario de venta debe ser mayor a cero.")
        if minimo < 0 or minimo > precio:
            raise ValueError("El precio mínimo debe ser mayor o igual a cero y no puede superar el precio unitario.")
        extras = extra_prices_from_data(request.form)
        with db_transaction() as (c, _):
            ensure_extra_price_columns(c)
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
            c.execute(
                """
                INSERT INTO producto_precios
                    (cliente_id,producto_id,producto_presentacion_id,precio_venta_estandar,precio_minimo_venta,
                     precio_cuarta,precio_media,precio_docena,precio_caja,moneda,vigente_desde,estado,created_at,updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'BOB',NOW(),%s,NOW(),NOW())
                """,
                (g.user["cliente_id"], product_id, presentation_id, precio, minimo, extras.get("precio_cuarta"), extras.get("precio_media"), extras.get("precio_docena"), extras.get("precio_caja"), precio_activo),
            )
            log_audit(c, cliente_id=g.user["cliente_id"], usuario_id=g.user["id"], modulo="CATALOGO", accion="EDITAR_PRESENTACION_PRECIO", tabla_afectada="producto_precios", registro_id=presentation_id, valor_anterior=previous_presentation, valor_nuevo={"producto_id": product_id, "presentacion": presentacion, "precio": precio, "minimo": minimo, **extras})
        flash("Presentación y precios actualizados correctamente.", "success")
    except ValueError as e:
        flash(str(e), "danger")
    return redirect(url_for("inventory.index"))
