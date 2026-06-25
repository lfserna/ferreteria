from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app.services.user_service import (
    DEFAULT_USER_PASSWORD,
    create_user,
    get_form_options,
    get_user_quota,
    list_users,
    update_user,
)
from app.utils.security import login_required


users_bp = Blueprint("users", __name__, url_prefix="/usuarios")


def can_manage_users():
    return g.user and g.user["rol_codigo"] in {"ADMIN_GENERAL_NEGOCIO", "ADMIN_TIENDA"}


@users_bp.route("")
@login_required
def index():
    if not can_manage_users():
        flash("No tienes permisos para gestionar usuarios.", "danger")
        return redirect(url_for("dashboard.index"))
    quota = get_user_quota(g.user["cliente_id"])
    options = get_form_options(g.user["cliente_id"])
    users = list_users(g.user["cliente_id"])
    return render_template("users/index.html", users=users, quota=quota, options=options,
                           default_password=DEFAULT_USER_PASSWORD)


@users_bp.route("/crear", methods=["POST"])
@login_required
def crear():
    if not can_manage_users():
        flash("No tienes permisos para gestionar usuarios.", "danger")
        return redirect(url_for("dashboard.index"))
    try:
        result = create_user(g.user["cliente_id"], g.user["id"], request.form)
        flash(f"Usuario creado: {result['username']}. Contraseña por defecto: {result['default_password']}", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("users.index"))


@users_bp.route("/<int:user_id>/editar", methods=["POST"])
@login_required
def editar(user_id):
    if not can_manage_users():
        flash("No tienes permisos para gestionar usuarios.", "danger")
        return redirect(url_for("dashboard.index"))
    try:
        update_user(g.user["cliente_id"], g.user["id"], user_id, request.form)
        flash("Usuario actualizado correctamente.", "success")
    except ValueError as exc:
        flash(str(exc), "danger")
    return redirect(url_for("users.index"))
