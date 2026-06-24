from functools import wraps

from flask import flash, g, redirect, url_for


ADMIN_ROLES = {"ADMIN_GENERAL_NEGOCIO", "ADMIN_TIENDA"}
PRODUCT_MANAGER_ROLES = {"ADMIN_GENERAL_NEGOCIO", "ADMIN_TIENDA"}
INVENTORY_MANAGER_ROLES = {"ADMIN_GENERAL_NEGOCIO", "ADMIN_TIENDA", "ADMIN_ALMACEN"}
ALERT_MANAGER_ROLES = {"ADMIN_GENERAL_NEGOCIO", "ADMIN_TIENDA"}


def current_role():
    return g.user.get("rol_codigo") if g.get("user") else None


def has_role(*roles):
    return current_role() in set(roles)


def require_roles(*roles):
    allowed = set(roles)

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not g.get("user") or g.user.get("rol_codigo") not in allowed:
                flash("No tienes permisos para realizar esta acción.", "danger")
                return redirect(url_for("dashboard.index"))
            return view(*args, **kwargs)
        return wrapped
    return decorator


def can_manage_products():
    return current_role() in PRODUCT_MANAGER_ROLES


def can_manage_inventory():
    return current_role() in INVENTORY_MANAGER_ROLES


def can_manage_alerts():
    return current_role() in ALERT_MANAGER_ROLES
