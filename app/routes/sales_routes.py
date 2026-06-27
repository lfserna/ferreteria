from io import BytesIO

from flask import Blueprint, flash, g, jsonify, make_response, redirect, render_template, request, url_for
from mysql.connector.errors import DatabaseError
from xhtml2pdf import pisa

from app.database import db_cursor
from app.services.cash_service import cash_summary, close_cash_session, open_cash_session, require_open_cash
from app.services.context_service import get_primary_stock_location
from app.services.product_service import search_products
from app.services.receipt_service import get_sale_receipt
from app.services.sales_service import (
    confirm_sale_from_cart,
    create_order_from_cart,
    get_order,
    list_available_sellers,
    list_pending_orders,
)
from app.utils.security import login_required
from app.utils.serializers import to_jsonable

sales_bp = Blueprint("sales", __name__, url_prefix="/ventas")
SALES_ROLES = {"ADMIN_TIENDA", "CAJERO", "VENDEDOR"}
CASH_REQUIRED_ROLES = {"ADMIN_TIENDA", "CAJERO"}


def can_access_sales(): return g.user and g.user.get("rol_codigo") in SALES_ROLES
def can_confirm_sales(): return g.user["rol_codigo"] in CASH_REQUIRED_ROLES
def current_stock_location(): return get_primary_stock_location(g.user["cliente_id"], g.user.get("sucursal_id"), g.user.get("almacen_id"))

def current_seller_name():
    name = " ".join([part for part in [g.user.get("nombres"), g.user.get("apellido_paterno")] if part])
    return name or g.user.get("username") or "-"

def selected_seller_id_from_payload(payload):
    raw = payload.get("vendedor_id")
    if raw in (None, "", 0, "0"): return None
    try: return int(raw)
    except (TypeError, ValueError): return None

def ddl_temporal(exc):
    return getattr(exc, "errno", None) == 1684 or "concurrent DDL" in str(exc) or "definition is being modified" in str(exc)

def sales_state_payload(ubicacion_stock_id=None):
    if not can_confirm_sales(): return {"caja": None, "pending_orders": []}
    if ubicacion_stock_id is None: ubicacion_stock_id = current_stock_location()
    try:
        caja = cash_summary(g.user["cliente_id"], g.user["id"], ubicacion_stock_id)
    except DatabaseError as exc:
        if not ddl_temporal(exc): raise
        caja = None
    return {"caja": caja, "pending_orders": list_pending_orders(g.user["cliente_id"], g.user.get("sucursal_id"))}


@sales_bp.route("")
@login_required
def index():
    if not can_access_sales(): return redirect(url_for("dashboard.index"))
    ubicacion_stock_id = current_stock_location(); can_confirm = can_confirm_sales(); can_send_order = g.user["rol_codigo"] in {"VENDEDOR", "ADMIN_TIENDA"}
    pending_orders = list_pending_orders(g.user["cliente_id"], g.user.get("sucursal_id")) if can_confirm else []
    sellers = list_available_sellers(g.user["cliente_id"], g.user.get("sucursal_id")) if can_confirm else []
    caja = cash_summary(g.user["cliente_id"], g.user["id"], ubicacion_stock_id) if can_confirm else None
    default_seller_id = g.user["id"] if g.user.get("rol_codigo") == "VENDEDOR" else ""
    default_seller_name = current_seller_name() if default_seller_id else "Sin vendedor"
    return render_template("sales/index.html", can_confirm=can_confirm, can_send_order=can_send_order, pending_orders=pending_orders, sellers=sellers, caja=caja, caja_abierta=bool(caja), requiere_caja=can_confirm, ubicacion_stock_id=ubicacion_stock_id, seller_id=default_seller_id, seller_name=default_seller_name)


@sales_bp.route("/caja/abrir", methods=["POST"])
@login_required
def abrir_caja():
    if not can_confirm_sales(): flash("Tu rol no requiere apertura de caja.", "danger"); return redirect(url_for("sales.index"))
    ubicacion_stock_id = current_stock_location()
    if not ubicacion_stock_id: flash("El usuario no tiene una ubicación de stock configurada.", "danger"); return redirect(url_for("sales.index"))
    try: open_cash_session(g.user["cliente_id"], g.user["id"], ubicacion_stock_id, request.form.get("monto_inicial_efectivo"), request.form.get("monto_inicial_qr"), request.form.get("observacion")); flash("Caja abierta correctamente. Ya puedes realizar ventas.", "success")
    except ValueError as exc: flash(str(exc), "danger")
    return redirect(url_for("sales.index"))


@sales_bp.route("/caja/cerrar", methods=["POST"])
@login_required
def cerrar_caja():
    if not can_confirm_sales(): flash("Tu rol no puede cerrar caja.", "danger"); return redirect(url_for("sales.index"))
    try: close_cash_session(g.user["cliente_id"], g.user["id"], current_stock_location(), request.form.get("monto_final_efectivo"), request.form.get("monto_final_qr"), request.form.get("observacion")); flash("Caja cerrada correctamente.", "success")
    except ValueError as exc: flash(str(exc), "danger")
    return redirect(url_for("sales.index"))


@sales_bp.route("/api/productos")
@login_required
def api_productos():
    if not can_access_sales(): return jsonify({"error": "Tu rol no tiene acceso a ventas."}), 403
    products = search_products(g.user["cliente_id"], request.args.get("q", ""), limit=40, ubicacion_stock_id=current_stock_location())
    return jsonify(to_jsonable(products))


@sales_bp.route("/api/estado")
@login_required
def api_estado():
    if not can_access_sales(): return jsonify({"error": "Tu rol no tiene acceso a ventas."}), 403
    try: return jsonify(to_jsonable(sales_state_payload()))
    except DatabaseError as exc:
        if not ddl_temporal(exc): raise
        return jsonify({"caja": None, "pending_orders": [], "schema_busy": True})


@sales_bp.route("/api/ordenes/<int:orden_id>")
@login_required
def api_orden(orden_id):
    if not can_access_sales(): return jsonify({"error": "Tu rol no tiene acceso a ventas."}), 403
    order = get_order(g.user["cliente_id"], orden_id)
    if not order: return jsonify({"error": "Orden no encontrada."}), 404
    return jsonify(to_jsonable(order))


@sales_bp.route("/ordenes/enviar-caja", methods=["POST"])
@login_required
def enviar_caja():
    if g.user["rol_codigo"] not in {"VENDEDOR", "ADMIN_TIENDA"}: return jsonify({"error": "Tu rol no puede enviar órdenes a caja."}), 403
    payload = request.get_json(silent=True) or {}; ubicacion_stock_id = current_stock_location()
    if not ubicacion_stock_id: return jsonify({"error": "El usuario no tiene una ubicación de stock configurada."}), 400
    try:
        result = create_order_from_cart(cliente_id=g.user["cliente_id"], sucursal_id=g.user.get("sucursal_id"), ubicacion_stock_id=ubicacion_stock_id, vendedor_id=g.user["id"], created_by=g.user["id"], items=payload.get("items", []))
        return jsonify(to_jsonable(result)), 201
    except ValueError as exc: return jsonify({"error": str(exc)}), 400


@sales_bp.route("/confirmar", methods=["POST"])
@login_required
def confirmar():
    if not can_confirm_sales(): return jsonify({"error": "Tu rol no puede confirmar cobros."}), 403
    payload = request.get_json(silent=True) or {}; ubicacion_stock_id = current_stock_location()
    if not ubicacion_stock_id: return jsonify({"error": "El usuario no tiene una ubicación de stock configurada."}), 400
    try:
        caja = require_open_cash(g.user["cliente_id"], g.user["id"], ubicacion_stock_id)
        result = confirm_sale_from_cart(cliente_id=g.user["cliente_id"], sucursal_id=g.user.get("sucursal_id"), ubicacion_stock_id=ubicacion_stock_id, cajero_id=g.user["id"], vendedor_id=selected_seller_id_from_payload(payload), created_by=g.user["id"], items=payload.get("items", []), metodo_pago=payload.get("metodo_pago"), idempotency_key=payload.get("idempotency_key"), orden_id=payload.get("orden_id"), caja_sesion_id=(caja.get("session") or {}).get("id"), cliente_data=payload.get("cliente"))
        try: result.update(sales_state_payload(ubicacion_stock_id))
        except DatabaseError as exc:
            if not ddl_temporal(exc): raise
            result.update({"caja": None, "pending_orders": [], "schema_busy": True})
        return jsonify(to_jsonable(result)), 201
    except ValueError as exc: return jsonify({"error": str(exc)}), 400


@sales_bp.route("/buscar-comprobante")
@login_required
def buscar_comprobante():
    if not g.user or g.user.get("rol_codigo") not in {"ADMIN_GENERAL_NEGOCIO", "ADMIN_TIENDA"}: return redirect(url_for("dashboard.index"))
    raw_number = (request.args.get("numero_comprobante") or "").strip()
    if not raw_number: flash("Ingresa un número de comprobante para buscar.", "warning"); return redirect(url_for("dashboard.index"))
    digits = "".join(ch for ch in raw_number if ch.isdigit())
    if not digits: flash("El número de comprobante debe contener dígitos.", "warning"); return redirect(url_for("dashboard.index"))
    receipt_number = int(digits); params = [g.user["cliente_id"], receipt_number, raw_number, digits]; filters = ["cliente_id=%s", "(numero_comprobante=%s OR numero_venta=%s OR numero_venta=%s)"]
    if g.user.get("rol_codigo") == "ADMIN_TIENDA": filters.append("sucursal_id=%s"); params.append(g.user.get("sucursal_id"))
    with db_cursor() as cursor: cursor.execute(f"SELECT id FROM ventas WHERE {' AND '.join(filters)} ORDER BY fecha_venta DESC, id DESC LIMIT 1", tuple(params)); row = cursor.fetchone()
    if not row: flash(f"No se encontró el comprobante {raw_number} dentro de tu alcance.", "warning"); return redirect(url_for("dashboard.index"))
    return redirect(url_for("sales.comprobante", venta_id=row["id"]))


@sales_bp.route("/<int:venta_id>/comprobante")
@login_required
def comprobante(venta_id):
    if not g.user or g.user.get("rol_codigo") == "ADMIN_ALMACEN": return redirect(url_for("inventory.index"))
    receipt = get_sale_receipt(g.user["cliente_id"], venta_id)
    if not receipt: return "Comprobante no encontrado", 404
    return render_template("sales/receipt_58mm.html", venta=receipt)


@sales_bp.route("/<int:venta_id>/comprobante-pdf")
@login_required
def comprobante_pdf(venta_id):
    if not g.user or g.user.get("rol_codigo") == "ADMIN_ALMACEN": return redirect(url_for("inventory.index"))
    receipt = get_sale_receipt(g.user["cliente_id"], venta_id)
    if not receipt: return "Comprobante no encontrado", 404
    html = render_template("sales/receipt_58mm.html", venta=receipt, pdf=True)
    pdf_buffer = BytesIO(); status = pisa.CreatePDF(html, dest=pdf_buffer, encoding="UTF-8")
    if status.err: return "No se pudo generar el PDF del comprobante.", 500
    response = make_response(pdf_buffer.getvalue()); response.headers["Content-Type"] = "application/pdf"; response.headers["Content-Disposition"] = f"inline; filename=comprobante-{receipt.get('numero_comprobante') or venta_id}.pdf"; return response
