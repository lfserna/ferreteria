from app.database import db_cursor
from app.utils.security import verify_password


def authenticate(username: str, password: str):
    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, cliente_id, username, password_hash, nombres, apellido_paterno, estado
            FROM usuarios
            WHERE username = %s AND estado = 'ACTIVO'
            LIMIT 1
            """,
            (username,),
        )
        user = cursor.fetchone()
        if not user or not verify_password(user["password_hash"], password):
            return None
        return user


def touch_last_login(user_id: int):
    with db_cursor(commit=True) as cursor:
        cursor.execute("UPDATE usuarios SET ultimo_login_at = NOW() WHERE id = %s", (user_id,))
