from flask import flash, g, jsonify, redirect, request, url_for
from mysql.connector.errors import IntegrityError

from app.routes.inventory_v2_routes import allowed_managed, qty, wants_json
from app.services.product_admin_service import create_product
from app.utils.permissions import can_manage_products
from app.utils.security import login_required


def duplicate_message(error: IntegrityError):
    text = str(error)
    lowered = text.lower()
    if "codigo_producto" in lowered:
        return "No se pudo guardar: el código general del producto ya existe. Déjalo vacío o usa otro."
    if "codigo_barras" in lowered:
        return "No se pudo guardar: el código de barras general del producto ya existe. Déjalo vacío o usa otro."
    if "producto_codigos" in lowered:
        return "El producto se intentó guardar con códigos individuales duplicados. Vuelve a intentar; los duplicados ya se omiten automáticamente."
    return f"No se pudo guardar por un dato duplicado en base de datos. Detalle: {text}"


@login_required
def crear_producto_fixed():
    if not can_manage_products():
        message = "No tienes permisos para crear productos."
        if wants_json():
            return jsonify({"ok": False, "message": message}), 403
        flash(message, "danger")
        return redirect(url_for("inventory.index"))

    try:
        cantidad = qty(request.form.get("cantidad_inicial"))
        ubicacion_id = int(request.form.get("ubicacion_stock_id") or 0)
        allowed_managed(ubicacion_id)
        data = request.form.copy()
        data["cantidad_inicial"] = str(cantidad)

        result = create_product(g.user["cliente_id"], g.user["id"], data)
        product_id = result["product_id"] if isinstance(result, dict) else result
        skipped = result.get("codes_skipped", []) if isinstance(result, dict) else []
        registered = result.get("codes_registered", 0) if isinstance(result, dict) else 0

        message = "Producto agregado correctamente al inventario."
        if skipped:
            message += f" Códigos registrados: {registered}. Códigos omitidos por existir previamente: {', '.join(skipped[:5])}"
            if len(skipped) > 5:
                message += f" y {len(skipped) - 5} más."

        if wants_json():
            return jsonify({"ok": True, "message": message, "product_id": product_id, "codes_skipped": skipped})
        flash(message, "success" if not skipped else "warning")
    except IntegrityError as e:
        message = duplicate_message(e)
        if wants_json():
            return jsonify({"ok": False, "message": message}), 400
        flash(message, "danger")
    except ValueError as e:
        if wants_json():
            return jsonify({"ok": False, "message": str(e)}), 400
        flash(str(e), "danger")

    return redirect(url_for("inventory.index"))
