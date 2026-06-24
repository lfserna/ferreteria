import re
import unicodedata

from werkzeug.security import generate_password_hash

from app.database import db_cursor, db_transaction
from app.services.audit_service import log_audit

DEFAULT_USER_PASSWORD = "hola123"


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-zA-Z0-9]", "", value)
    return value.lower()


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
            return {"max_usuarios": 0, "usuarios_actuales": 0, "disponibles": 0}
        row["disponibles"] = max(int(row["max_usuarios"]) - int(row["usuarios_actuales"]), 0)
        return row


def list_users(cliente_id: int):
    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT u.id, u.username, u.nombres, u.apellido_paterno, u.apellido_materno,
                   u.edad, u.celular, u.email, u.estado, r.nombre AS rol,
                   s.nombre AS sucursal, a.nombre AS almacen
            FROM usuarios u
            LEFT JOIN usuario_roles ur ON ur.usuario_id = u.id AND ur.estado = 'ACTIVO'
            LEFT JOIN roles r ON r.id = ur.rol_id
            LEFT JOIN sucursales s ON s.id = ur.sucursal_id
            LEFT JOIN almacenes a ON a.id = ur.almacen_id
            WHERE u.cliente_id = %s AND u.deleted_at IS NULL
            ORDER BY u.created_at DESC, u.id DESC
            """,
            (cliente_id,),
        )
        return cursor.fetchall()


def get_form_options(cliente_id: int):
    with db_cursor() as cursor:
        cursor.execute("SELECT id, codigo, nombre FROM roles WHERE estado = 'ACTIVO' ORDER BY nivel DESC")
        roles = cursor.fetchall()
        cursor.execute("SELECT id, nombre FROM sucursales WHERE cliente_id = %s AND estado = 'ACTIVO' ORDER BY nombre", (cliente_id,))
        sucursales = cursor.fetchall()
        cursor.execute("SELECT id, nombre FROM almacenes WHERE cliente_id = %s AND estado = 'ACTIVO' ORDER BY nombre", (cliente_id,))
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

        cursor.execute(
            """
            INSERT INTO usuarios
                (cliente_id, sucursal_id, username, password_hash, nombres, apellido_paterno,
                 apellido_materno, edad, celular, email, estado, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVO',NOW(),NOW())
            """,
            (cliente_id, resolved_sucursal_id, username, password_hash, nombres, apellido_paterno,
             apellido_materno, edad, celular, email),
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
