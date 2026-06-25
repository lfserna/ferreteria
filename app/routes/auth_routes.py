from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.database import db_cursor
from app.services.audit_service import log_audit
from app.services.auth_service import authenticate, change_user_password as change_key, touch_last_login


auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = authenticate(username, password)
        if not user:
            flash("Usuario o contraseña incorrectos.", "danger")
            return render_template("login.html", username=username), 401
        session.clear()
        session["user_id"] = user["id"]
        session["cliente_id"] = user["cliente_id"]
        if user.get("uses_default_password"):
            session["force_key_change"] = True
        touch_last_login(user["id"])
        with db_cursor(commit=True) as cursor:
            log_audit(cursor, cliente_id=user["cliente_id"], usuario_id=user["id"], modulo="AUTH", accion="LOGIN", tabla_afectada="usuarios", registro_id=user["id"], valor_nuevo={"estado": "OK"})
        if user.get("uses_default_password"):
            flash("Debes cambiar tu contraseña antes de usar el sistema.", "warning")
            return redirect(url_for("auth.cambiar_clave"))
        if user.get("rol_codigo") == "ADMIN_ALMACEN":
            return redirect(url_for("inventory.index"))
        return redirect(url_for("dashboard.index"))
    return render_template("login.html")


@auth_bp.route("/cambiar-clave", methods=["GET", "POST"])
def cambiar_clave():
    user_id = session.get("user_id")
    if not user_id:
        flash("Inicia sesión para continuar.", "warning")
        return redirect(url_for("auth.login"))
    if request.method == "POST":
        try:
            change_key(user_id, request.form.get("clave_actual", ""), request.form.get("clave_nueva", ""), request.form.get("clave_confirmacion", ""))
            session.pop("force_key_change", None)
            flash("Contraseña actualizada correctamente.", "success")
            return redirect(url_for("dashboard.index"))
        except ValueError as exc:
            flash(str(exc), "danger")
    return render_template("change_password.html", forced=session.get("force_key_change"))


@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada correctamente.", "success")
    return redirect(url_for("auth.login"))
