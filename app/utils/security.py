from functools import wraps

from flask import flash, redirect, session, url_for
from werkzeug.security import check_password_hash

try:
    import bcrypt
except ImportError:  # pragma: no cover
    bcrypt = None


def verify_password(stored_hash: str, plain_text: str) -> bool:
    if not stored_hash:
        return False

    stored_hash = stored_hash.strip()

    if stored_hash.startswith(("$2a$", "$2b$", "$2y$")):
        if bcrypt is None:
            return False
        normalized_hash = stored_hash.replace("$2y$", "$2b$", 1)
        return bcrypt.checkpw(plain_text.encode("utf-8"), normalized_hash.encode("utf-8"))

    try:
        return check_password_hash(stored_hash, plain_text)
    except ValueError:
        return False


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not session.get("user_id"):
            flash("Inicia sesión para continuar.", "warning")
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped_view
