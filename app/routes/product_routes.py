from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for

from app.services.category_admin_service import (
    create_brand,
    create_category,
    list_brands,
    list_categories_admin,
    update_brand,
    update_category,
)
from app.services.product_admin_service import create_product, get_product, list_products_admin, update_product
from app.services.product_service import search_products
from app.utils.permissions import can_manage_products
from app.utils.security import login_required
from app.utils.serializers import to_jsonable


products_bp = Blueprint("products", __name__, url_prefix="/productos")


@products_bp.route("")
@login_required
def index():
    products = search_products(g.user["cliente_id"], request.args.get("q", ""), limit=80)
    return render_template("products/index.html", products=products, can_manage=can_manage_products())


@products_bp.route("/buscar")
@login_required
def buscar():
    products = search_products(g.user["cliente_id"], request.args.get("q", ""), limit=50)
    return jsonify(to_jsonable(products))


@products_bp.route("/admin")
@login_required
def admin():
    if not can_manage_products():
        flash("No tienes permisos para administrar productos.", "danger")
        return redirect(url_for("products.index"))
    return render_template(
        "products/admin.html",
        products=list_products_admin(g.user["cliente_id"], request.args.get("q", "")),
        categorias=list_categories_admin(g.user["cliente_id"]),
        marcas=list_brands(g.user["cliente_id"]),
    )


@products_bp.route("/crear", methods=["POST"])
@login_required
def crear_producto():
    if not can_manage_products():
        flash("No tienes permisos para crear productos.", "danger")
        return redirect(url_for("products.index"))
    try:
        create_product(g.user["cliente_id"], g.user["id"], request.form)
        flash("Producto creado correctamente.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("products.admin"))


@products_bp.route("/<int:product_id>/editar", methods=["GET", "POST"])
@login_required
def editar_producto(product_id):
    if not can_manage_products():
        flash("No tienes permisos para editar productos.", "danger")
        return redirect(url_for("products.index"))
    if request.method == "POST":
        try:
            update_product(g.user["cliente_id"], g.user["id"], product_id, request.form)
            flash("Producto actualizado correctamente.", "success")
            return redirect(url_for("products.admin"))
        except ValueError as exc:
            flash(str(exc), "danger")
    product = get_product(g.user["cliente_id"], product_id)
    if not product:
        flash("Producto no encontrado.", "danger")
        return redirect(url_for("products.admin"))
    return render_template("products/edit.html", product=product, categorias=list_categories_admin(g.user["cliente_id"]), marcas=list_brands(g.user["cliente_id"]))


@products_bp.route("/categorias/crear", methods=["POST"])
@login_required
def crear_categoria():
    if not can_manage_products():
        flash("No tienes permisos para crear categorías.", "danger")
        return redirect(url_for("products.admin"))
    try:
        create_category(g.user["cliente_id"], g.user["id"], request.form)
        flash("Categoría creada correctamente.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("products.admin"))


@products_bp.route("/categorias/<int:category_id>/editar", methods=["POST"])
@login_required
def editar_categoria(category_id):
    if not can_manage_products():
        flash("No tienes permisos para editar categorías.", "danger")
        return redirect(url_for("products.admin"))
    try:
        update_category(g.user["cliente_id"], g.user["id"], category_id, request.form)
        flash("Categoría actualizada correctamente.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("products.admin"))


@products_bp.route("/marcas/crear", methods=["POST"])
@login_required
def crear_marca():
    if not can_manage_products():
        flash("No tienes permisos para crear marcas.", "danger")
        return redirect(url_for("products.admin"))
    try:
        create_brand(g.user["cliente_id"], g.user["id"], request.form)
        flash("Marca creada correctamente.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("products.admin"))


@products_bp.route("/marcas/<int:brand_id>/editar", methods=["POST"])
@login_required
def editar_marca(brand_id):
    if not can_manage_products():
        flash("No tienes permisos para editar marcas.", "danger")
        return redirect(url_for("products.admin"))
    try:
        update_brand(g.user["cliente_id"], g.user["id"], brand_id, request.form)
        flash("Marca actualizada correctamente.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("products.admin"))
