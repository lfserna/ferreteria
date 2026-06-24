from io import BytesIO

from flask import Blueprint, g, jsonify, make_response, render_template, request
from xhtml2pdf import pisa

from app.services.context_service import get_primary_stock_location
from app.services.product_service import search_products
from app.services.sales_service import confirm_sale_from_cart, create_order_from_cart, get_order, get_sale_receipt, list_pending_orders
from app.utils.security import login_required
from app.utils.serializers import to_jsonable


sales_bp = Blueprint("sales", __name__, url_prefix="/ventas")


@sales_bp.route("")
@login_required
def index():
    can_confirm = g.user["rol_codigo"] in {"ADMIN_GENERAL_NEGOCIO", "ADMIN_TIENDA", "CAJERO"}
    can_send_order = g.user["rol_codigo"] in {"VENDEDOR", "ADMIN_TIENDA", "CAJERO"}
    pending_orders = list_pending_orders(g.user["cliente_id"], g.user.get("sucursal_id")) if can_confirm else []
    return render_template("sales/index.html", can_confirm=can_confirm, can_send_order=can_send_order, pending_orders=pending_orders)


@sales_bp.route("/api/productos")
@login_required
def api_productos():
    products = search_products(g.user["cliente_id"], request.args.get("q", ""), limit=40)
    return jsonify(to_jsonable(products))


@sales_bp.route("/api/ordenes/<int:orden_id>")
@login_required
def api_orden(orden_id):
    order = get_order(g.user["cliente_id"], orden_id)
    if not order:
        return jsonify({"error": "Orden no encontrada."}), 404
    return jsonify(to_jsonable(order))


@sales_bp.route("/ordenes/enviar-caja", methods=["POST"])
@login_required
def enviar_caja():
    payload = request.get_json(silent=True) or {}
    ubicacion_stock_id = get_primary_stock_location(g.user["cliente_id"], g.user.get("sucursal_id"), g.user.get("almacen_id"))
    if not ubicacion_stock_id:
        return jsonify({"error": "El usuario no tiene una ubicación de stock configurada."}), 400
    try:
        result = create_order_from_cart(cliente_id=g.user["cliente_id"], sucursal_id=g.user.get("sucursal_id"), ubicacion_stock_id=ubicacion_stock_id,
                                        vendedor_id=g.user["id"], created_by=g.user["id"], items=payload.get("items", []))
        return jsonify(to_jsonable(result)), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@sales_bp.route("/confirmar", methods=["POST"])
@login_required
def confirmar():
    if g.user["rol_codigo"] not in {"ADMIN_GENERAL_NEGOCIO", "ADMIN_TIENDA", "CAJERO"}:
        return jsonify({"error": "Tu rol no puede confirmar cobros."}), 403
    payload = request.get_json(silent=True) or {}
    ubicacion_stock_id = get_primary_stock_location(g.user["cliente_id"], g.user.get("sucursal_id"), g.user.get("almacen_id"))
    if not ubicacion_stock_id:
        return jsonify({"error": "El usuario no tiene una ubicación de stock configurada."}), 400
    try:
        result = confirm_sale_from_cart(cliente_id=g.user["cliente_id"], sucursal_id=g.user.get("sucursal_id"), ubicacion_stock_id=ubicacion_stock_id,
                                        cajero_id=g.user["id"], vendedor_id=None, created_by=g.user["id"], items=payload.get("items", []),
                                        metodo_pago=payload.get("metodo_pago"), idempotency_key=payload.get("idempotency_key"), orden_id=payload.get("orden_id"))
        return jsonify(to_jsonable(result)), 201
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@sales_bp.route("/<int:venta_id>/comprobante")
@login_required
def comprobante(venta_id):
    receipt = get_sale_receipt(g.user["cliente_id"], venta_id)
    if not receipt:
        return "Comprobante no encontrado", 404
    return render_template("sales/receipt_58mm.html", venta=receipt)


@sales_bp.route("/<int:venta_id>/comprobante-pdf")
@login_required
def comprobante_pdf(venta_id):
    receipt = get_sale_receipt(g.user["cliente_id"], venta_id)
    if not receipt:
        return "Comprobante no encontrado", 404
    html = render_template("sales/receipt_58mm.html", venta=receipt, pdf=True)
    pdf_buffer = BytesIO()
    status = pisa.CreatePDF(html, dest=pdf_buffer, encoding="UTF-8")
    if status.err:
        return "No se pudo generar el PDF del comprobante.", 500
    response = make_response(pdf_buffer.getvalue())
    response.headers["Content-Type"] = "application/pdf"
    response.headers["Content-Disposition"] = f"inline; filename=comprobante-{venta_id}.pdf"
    return response
