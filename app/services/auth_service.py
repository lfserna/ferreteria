from werkzeug.security import generate_password_hash

from app.database import db_cursor
from app.utils.security import verify_password

DEFAULT_USER_PASSWORD = "hola123"


def table_columns(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    return {row["Field"] for row in cursor.fetchall()}


def authenticate(username: str, password: str):
    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT u.id, u.cliente_id, u.username, u.password_hash, u.nombres,
                   u.apellido_paterno, u.estado, r.codigo AS rol_codigo
            FROM usuarios u
            LEFT JOIN usuario_roles ur ON ur.usuario_id = u.id AND ur.estado = 'ACTIVO'
            LEFT JOIN roles r ON r.id = ur.rol_id
            WHERE u.username = %s AND u.estado = 'ACTIVO'
            ORDER BY ur.id ASC
            LIMIT 1
            """,
            (username,),
        )
        user = cursor.fetchone()
        if not user or not verify_password(user["password_hash"], password):
            return None
        user["uses_default_password"] = verify_password(user["password_hash"], DEFAULT_USER_PASSWORD)
        return user


def get_user_auth(user_id: int):
    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, cliente_id, username, password_hash, nombres, apellido_paterno
            FROM usuarios
            WHERE id = %s AND estado = 'ACTIVO'
            LIMIT 1
            """,
            (user_id,),
        )
        return cursor.fetchone()


def user_uses_default_password(user_id: int) -> bool:
    user = get_user_auth(user_id)
    return bool(user and verify_password(user.get("password_hash"), DEFAULT_USER_PASSWORD))


def change_user_password(user_id: int, current_password: str, new_password: str, confirmation: str):
    user = get_user_auth(user_id)
    if not user:
        raise ValueError("Usuario no encontrado o inactivo.")
    if not verify_password(user["password_hash"], current_password or ""):
        raise ValueError("La contraseña actual no es correcta.")
    new_password = new_password or ""
    confirmation = confirmation or ""
    if len(new_password) < 6:
        raise ValueError("La nueva contraseña debe tener al menos 6 caracteres.")
    if new_password != confirmation:
        raise ValueError("La confirmación no coincide con la nueva contraseña.")
    if new_password == DEFAULT_USER_PASSWORD:
        raise ValueError("La nueva contraseña no puede ser la contraseña por defecto.")
    with db_cursor(commit=True) as cursor:
        cols = table_columns(cursor, "usuarios")
        password_hash = generate_password_hash(new_password)
        if "updated_at" in cols:
            cursor.execute("UPDATE usuarios SET password_hash=%s, updated_at=NOW() WHERE id=%s", (password_hash, user_id))
        else:
            cursor.execute("UPDATE usuarios SET password_hash=%s WHERE id=%s", (password_hash, user_id))


def touch_last_login(user_id: int):
    with db_cursor(commit=True) as cursor:
        cols = table_columns(cursor, "usuarios")
        if "ultimo_login_at" in cols:
            cursor.execute("UPDATE usuarios SET ultimo_login_at = NOW() WHERE id = %s", (user_id,))
