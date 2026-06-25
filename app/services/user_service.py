import re
import unicodedata

from werkzeug.security import generate_password_hash

from app.database import db_cursor, db_transaction
from app.services.audit_service import log_audit

DEFAULT_USER_PASSWORD = "hola123"
DEFAULT_MAX_USERS = 10


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]", "", value)
    return value.lower()


def table_columns(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    return {row["Field"] for row in cursor.fetchall()}


def column_exists(cursor, table_name, column_name):
    return column_name in table_columns(cursor, table_name)


def ensure_user_quota_column(cursor=None):
    def apply(cur):
        if not column_exists(cur, "clientes", "max_usuarios"):
            cur.execute(f"ALTER TABLE clientes ADD COLUMN max_usuarios INT NOT NULL DEFAULT {DEFAULT_MAX_USERS}")
        cur.execute("UPDATE clientes SET max_usuarios=%s WHERE max_usuarios IS NULL OR max_usuarios <= 0", (DEFAULT_MAX_USERS,))
    if cursor is not None:
        apply(cursor)
    else:
        with db_cursor(commit=True) as cur:
            apply(cur)


def generate_username(cursor, cliente_id: int, nombres: str, apellido_paterno: str) -> str:
    first_name = normalize_text((nombres or "").strip().split()[0] if nombres else "")
    first_lastname = normalize_text((apellido_paterno or "").strip().split()[0] if apellido_paterno else "")
    if not first_name or not first_lastname:
        raise ValueError("Para generar el usuario se requiere nombre y apellido paterno.")
    base = f"{first_name[0]}{first_lastname}"
    username = base
    suffix = 2
    while True:
        cursor.execute(
            "SELECT id FROM usuarios WHERE cliente_id = %s AND username = %s LIMIT 1",
            (cliente_id, username),
        )
        if not cursor.fetchone():
            return username
        username = f"{base}{suffix}"
        suffix += 1


def get_user_quota(cliente_id: int):
    ensure_user_quota_column()
    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT c.max_usuarios,
                   COUNT(u.id) AS usuarios_actuales
            FROM clientes c
            LEFT JOIN usuarios u ON u.cliente_id = c.id AND u.deleted_at IS NULL AND u.estado <> 'INACTIVO'
            WHERE c.id = %s
            GROUP BY c.id, c.max_usuarios
            """,
            (cliente_id,),
        )
        row = cursor.fetchone()
        if not row:
            return {"max_usuarios": DEFAULT_MAX_USERS, "usuarios_actuales": 0, "disponibles": DEFAULT_MAX_USERS}
        row["max_usuarios"] = int(row.get("max_usuarios") or DEFAULT_MAX_USERS)
        row["usuarios_actuales"] = int(row.get("usuarios_actuales") or 0)
        row["disponibles"] = max(row["max_usuarios"] - row["usuarios_actuales"], 0)
        return row


def update_user_limit(cliente_id: int, current_user_id: int, max_usuarios: int):
    max_usuarios = int(max_usuarios or 0)
    if max_usuarios <= 0:
        raise ValueError("El límite de usuarios debe ser mayor a cero.")
    with db_transaction() as (cursor, _connection):
        ensure_user_quota_column(cursor)
        cursor.execute(
            "SELECT COUNT(*) AS total FROM usuarios WHERE cliente_id = %s AND deleted_at IS NULL AND estado <> 'INACTIVO'",
            (cliente_id,),
        )
        total_users = int(cursor.fetchone()["total"])
        if max_usuarios < total_users:
            raise ValueError(f"El límite no puede ser menor a los {total_users} usuarios activos actuales.")
        cursor.execute("SELECT max_usuarios FROM clientes WHERE id = %s FOR UPDATE", (cliente_id,))
        previous = cursor.fetchone()
        cols = table_columns(cursor, "clientes")
        if "updated_at" in cols:
            cursor.execute("UPDATE clientes SET max_usuarios = %s, updated_at = NOW() WHERE id = %s", (max_usuarios, cliente_id))
        else:
            cursor.execute("UPDATE clientes SET max_usuarios = %s WHERE id = %s", (max_usuarios, cliente_id))
        log_audit(cursor, cliente_id=cliente_id, usuario_id=current_user_id, modulo="CLIENTES",
                  accion="ACTUALIZAR_LIMITE_USUARIOS", tabla_afectada="clientes", registro_id=cliente_id,
                  valor_anterior=previous, valor_nuevo={"max_usuarios": max_usuarios})


def optional_user_field(user_cols, field_name, alias=None, default="NULL"):
    alias = alias or field_name
    if field_name in user_cols:
        return f"u.{field_name} AS {alias}"
    return f"{default} AS {alias}"


def first_existing_scope_expr(user_cols, role_cols, field_name):
    candidates = []
    if field_name in role_cols:
        candidates.append(f"ur.{field_name}")
    if field_name in user_cols:
        candidates.append(f"u.{field_name}")
    if not candidates:
        return "NULL"
    if len(candidates) == 1:
        return candidates[0]
    return f"COALESCE({', '.join(candidates)})"


def list_users(cliente_id: int):
    with db_cursor() as cursor:
        user_cols = table_columns(cursor, "usuarios")
        role_cols = table_columns(cursor, "usuario_roles")
        sucursal_expr = first_existing_scope_expr(user_cols, role_cols, "sucursal_id")
        almacen_expr = first_existing_scope_expr(user_cols, role_cols, "almacen_id")
        deleted_filter = "u.deleted_at IS NULL" if "deleted_at" in user_cols else "1=1"
        order_expr = "u.created_at DESC, u.id DESC" if "created_at" in user_cols else "u.id DESC"
        cursor.execute(
            f"""
            SELECT u.id,
                   {optional_user_field(user_cols, 'username')},
                   {optional_user_field(user_cols, 'nombres')},
                   {optional_user_field(user_cols, 'apellido_paterno')},
                   {optional_user_field(user_cols, 'apellido_materno')},
                   {optional_user_field(user_cols, 'edad')},
                   {optional_user_field(user_cols, 'celular')},
                   {optional_user_field(user_cols, 'email')},
                   {optional_user_field(user_cols, 'estado', default="'ACTIVO'")},
                   r.nombre AS rol,
                   s.nombre AS sucursal,
                   a.nombre AS almacen
            FROM usuarios u
            LEFT JOIN usuario_roles ur ON ur.usuario_id = u.id AND ur.estado = 'ACTIVO'
            LEFT JOIN roles r ON r.id = ur.rol_id
            LEFT JOIN sucursales s ON s.id = {sucursal_expr}
            LEFT JOIN almacenes a ON a.id = {almacen_expr}
            WHERE u.cliente_id = %s AND {deleted_filter}
            ORDER BY {order_expr}
            """,
            (cliente_id,),
        )
        return cursor.fetchall()


def get_form_options(cliente_id: int):
    with db_cursor() as cursor:
        cursor.execute("SELECT id, codigo, nombre FROM roles WHERE estado = 'ACTIVO' ORDER BY nivel DESC")
        roles = cursor.fetchall()
        cursor.execute("SELECT id, nombre FROM sucursales WHERE cliente_id = %s AND estado IN ('ACTIVO','ACTIVA') ORDER BY nombre", (cliente_id,))
        sucursales = cursor.fetchall()
        cursor.execute("SELECT id, nombre FROM almacenes WHERE cliente_id = %s AND estado IN ('ACTIVO','ACTIVA') ORDER BY nombre", (cliente_id,))
        almacenes = cursor.fetchall()
    return {"roles": roles, "sucursales": sucursales, "almacenes": almacenes}


def resolve_scope(role_code: str, sucursal_id, almacen_id):
    if role_code == "ADMIN_GENERAL_NEGOCIO":
        return "CLIENTE", None, None
    if role_code == "ADMIN_ALMACEN":
        if not almacen_id:
            raise ValueError("El administrador de almacén requiere seleccionar un almacén.")
        return "ALMACEN", None, int(almacen_id)
    if not sucursal_id:
        raise ValueError("Este rol requiere seleccionar una sucursal.")
    return "SUCURSAL", int(sucursal_id), None


def create_user(cliente_id: int, current_user_id: int, data: dict):
    nombres = (data.get("nombres") or "").strip()
    apellido_paterno = (data.get("apellido_paterno") or "").strip()
    apellido_materno = (data.get("apellido_materno") or "").strip()
    celular = (data.get("celular") or "").strip()
    email = (data.get("email") or "").strip() or None
    edad = data.get("edad") or None
    role_id = int(data.get("rol_id") or 0)
    sucursal_id = data.get("sucursal_id") or None
    almacen_id = data.get("almacen_id") or None

    if not nombres or not apellido_paterno:
        raise ValueError("Nombre y apellido paterno son obligatorios.")
    if not role_id:
        raise ValueError("Selecciona un rol para el usuario.")

    with db_transaction() as (cursor, _connection):
        ensure_user_quota_column(cursor)
        cursor.execute("SELECT max_usuarios FROM clientes WHERE id = %s FOR UPDATE", (cliente_id,))
        cliente = cursor.fetchone()
        if not cliente:
            raise ValueError("Cliente no encontrado.")
        cursor.execute(
            "SELECT COUNT(*) AS total FROM usuarios WHERE cliente_id = %s AND deleted_at IS NULL AND estado <> 'INACTIVO'",
            (cliente_id,),
        )
        total_users = int(cursor.fetchone()["total"])
        if total_users >= int(cliente["max_usuarios"]):
            raise ValueError(f"Este cliente ya llegó al límite de {cliente['max_usuarios']} usuarios.")

        cursor.execute("SELECT id, codigo FROM roles WHERE id = %s AND estado = 'ACTIVO' LIMIT 1", (role_id,))
        role = cursor.fetchone()
        if not role:
            raise ValueError("Rol inválido.")

        alcance, resolved_sucursal_id, resolved_almacen_id = resolve_scope(role["codigo"], sucursal_id, almacen_id)
        username = generate_username(cursor, cliente_id, nombres, apellido_paterno)
        password_hash = generate_password_hash(DEFAULT_USER_PASSWORD)
        user_cols = table_columns(cursor, "usuarios")
        insert_cols = ["cliente_id", "username", "password_hash", "nombres", "apellido_paterno", "apellido_materno", "edad", "celular", "email", "estado", "created_at", "updated_at"]
        values = {
            "cliente_id": cliente_id,
            "username": username,
            "password_hash": password_hash,
            "nombres": nombres,
            "apellido_paterno": apellido_paterno,
            "apellido_materno": apellido_materno,
            "edad": edad,
            "celular": celular,
            "email": email,
            "estado": "ACTIVO",
            "created_at": "NOW()",
            "updated_at": "NOW()",
        }
        if "sucursal_id" in user_cols:
            insert_cols.insert(1, "sucursal_id")
            values["sucursal_id"] = resolved_sucursal_id
        if "almacen_id" in user_cols:
            insert_cols.insert(2 if "sucursal_id" in user_cols else 1, "almacen_id")
            values["almacen_id"] = resolved_almacen_id
        insert_cols = [col for col in insert_cols if col in user_cols]
        raw = {"created_at", "updated_at"}
        placeholders = []
        params = []
        for col in insert_cols:
            if col in raw:
                placeholders.append(values[col])
            else:
                placeholders.append("%s")
                params.append(values[col])
        cursor.execute(
            f"INSERT INTO usuarios ({', '.join(insert_cols)}) VALUES ({', '.join(placeholders)})",
            tuple(params),
        )
        user_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO usuario_roles
                (cliente_id, usuario_id, rol_id, alcance, sucursal_id, almacen_id, estado, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,'ACTIVO',NOW(),NOW())
            """,
            (cliente_id, user_id, role_id, alcance, resolved_sucursal_id, resolved_almacen_id),
        )
        log_audit(cursor, cliente_id=cliente_id, usuario_id=current_user_id, modulo="USUARIOS",
                  accion="CREAR_USUARIO", tabla_afectada="usuarios", registro_id=user_id,
                  valor_nuevo={"username": username, "rol_id": role_id, "alcance": alcance})
        return {"id": user_id, "username": username, "default_password": DEFAULT_USER_PASSWORD}
