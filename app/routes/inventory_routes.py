from flask import Blueprint, g, jsonify, render_template, request

from app.services.product_service import list_stock
from app.utils.security import login_required
from app.utils.serializers import to_jsonable


inventory_bp = Blueprint("inventory", __name__, url_prefix="/inventario")


@inventory_bp.route("")
@login_required
def index():
    rows = list_stock(g.user["cliente_id"], request.args.get("q", ""))
    return render_template("inventory/index.html", rows=rows)


@inventory_bp.route("/buscar")
@login_required
def buscar():
    return jsonify(to_jsonable(list_stock(g.user["cliente_id"], request.args.get("q", ""))))
