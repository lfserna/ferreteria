from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.database import db_cursor
from app.services.audit_service import log_audit
from app.services.auth_service import authenticate, touch_last_login


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
        touch_last_login(user["id"])
        with db_cursor(commit=True) as cursor:
            log_audit(cursor, cliente_id=user["cliente_id"], usuario_id=user["id"], modulo="AUTH", accion="LOGIN", tabla_afectada="usuarios", registro_id=user["id"], valor_nuevo={"estado": "OK"})
        return redirect(url_for("dashboard.index"))
    return render_template("login.html")


@auth_bp.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada correctamente.", "success")
    return redirect(url_for("auth.login"))
