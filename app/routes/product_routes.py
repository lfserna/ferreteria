from flask import Blueprint, g, jsonify, render_template, request

from app.services.product_service import list_categories, search_products
from app.utils.security import login_required
from app.utils.serializers import to_jsonable


products_bp = Blueprint("products", __name__, url_prefix="/productos")


@products_bp.route("")
@login_required
def index():
    categories = list_categories(g.user["cliente_id"])
    products = search_products(g.user["cliente_id"], request.args.get("q", ""), limit=80)
    return render_template("products/index.html", categories=categories, products=products)


@products_bp.route("/buscar")
@login_required
def buscar():
    products = search_products(g.user["cliente_id"], request.args.get("q", ""), limit=50)
    return jsonify(to_jsonable(products))
