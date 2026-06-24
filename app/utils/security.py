from functools import wraps

from flask import flash, redirect, session, url_for
from werkzeug.security import check_password_hash


def verify_password(stored_hash: str, plain_text: str) -> bool:
    if not stored_hash:
        return False
    return check_password_hash(stored_hash, plain_text)


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("user_id"):
            flash("Inicia sesión para continuar.", "warning")
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped_view
