import re

from app.database import db_cursor, db_transaction
from app.services.audit_service import log_audit


def estado_value(cursor, table_name: str, requested: str = "ACTIVO"):
    cursor.execute(
        """
        SELECT COLUMN_TYPE, COLUMN_DEFAULT
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = 'estado'
        LIMIT 1
        """,
        (table_name,),
    )
    row = cursor.fetchone()
    if not row:
        return requested
    enum_values = re.findall(r"'([^']+)'", row["COLUMN_TYPE"] or "")
    if not enum_values:
        return requested
    normalized = (requested or "ACTIVO").upper()
    for value in enum_values:
        if value.upper() == normalized:
            return value
    fallback_map = {
        "ACTIVO": ["ACTIVA", "activo", "activa", "1"],
        "INACTIVO": ["INACTIVA", "inactivo", "inactiva", "0"],
    }
    for candidate in fallback_map.get(normalized, []):
        if candidate in enum_values:
            return candidate
    return row.get("COLUMN_DEFAULT") or enum_values[0]


def list_categories_admin(cliente_id: int):
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT id, nombre, descripcion, estado FROM categorias_producto WHERE cliente_id=%s AND deleted_at IS NULL ORDER BY nombre",
            (cliente_id,),
        )
        return cursor.fetchall()


def list_brands(cliente_id: int):
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT id, nombre, descripcion, estado FROM marcas WHERE cliente_id=%s AND deleted_at IS NULL ORDER BY nombre",
            (cliente_id,),
        )
        return cursor.fetchall()


def create_category(cliente_id: int, user_id: int, data: dict):
    nombre = (data.get("nombre") or "").strip()
    descripcion = (data.get("descripcion") or "").strip() or None
    if not nombre:
        raise ValueError("El nombre de la categoría es obligatorio.")
    with db_transaction() as (cursor, _connection):
        estado = estado_value(cursor, "categorias_producto", "ACTIVO")
        cursor.execute(
            "INSERT INTO categorias_producto (cliente_id, nombre, descripcion, estado, created_at, updated_at) VALUES (%s,%s,%s,%s,NOW(),NOW())",
            (cliente_id, nombre, descripcion, estado),
        )
        record_id = cursor.lastrowid
        log_audit(cursor, cliente_id=cliente_id, usuario_id=user_id, modulo="CATALOGO", accion="CREAR_CATEGORIA", tabla_afectada="categorias_producto", registro_id=record_id, valor_nuevo={"nombre": nombre, "estado": estado})
        return record_id


def update_category(cliente_id: int, user_id: int, record_id: int, data: dict):
    nombre = (data.get("nombre") or "").strip()
    descripcion = (data.get("descripcion") or "").strip() or None
    if not nombre:
        raise ValueError("El nombre de la categoría es obligatorio.")
    with db_transaction() as (cursor, _connection):
        estado = estado_value(cursor, "categorias_producto", data.get("estado") or "ACTIVO")
        cursor.execute("SELECT * FROM categorias_producto WHERE cliente_id=%s AND id=%s FOR UPDATE", (cliente_id, record_id))
        previous = cursor.fetchone()
        if not previous:
            raise ValueError("Categoría no encontrada.")
        cursor.execute("UPDATE categorias_producto SET nombre=%s, descripcion=%s, estado=%s, updated_at=NOW() WHERE id=%s", (nombre, descripcion, estado, record_id))
        log_audit(cursor, cliente_id=cliente_id, usuario_id=user_id, modulo="CATALOGO", accion="EDITAR_CATEGORIA", tabla_afectada="categorias_producto", registro_id=record_id, valor_anterior=previous, valor_nuevo={"nombre": nombre, "estado": estado})


def create_brand(cliente_id: int, user_id: int, data: dict):
    nombre = (data.get("nombre") or "").strip()
    descripcion = (data.get("descripcion") or "").strip() or None
    if not nombre:
        raise ValueError("El nombre de la marca es obligatorio.")
    with db_transaction() as (cursor, _connection):
        estado = estado_value(cursor, "marcas", "ACTIVO")
        cursor.execute(
            "INSERT INTO marcas (cliente_id, nombre, descripcion, estado, created_at, updated_at) VALUES (%s,%s,%s,%s,NOW(),NOW())",
            (cliente_id, nombre, descripcion, estado),
        )
        record_id = cursor.lastrowid
        log_audit(cursor, cliente_id=cliente_id, usuario_id=user_id, modulo="CATALOGO", accion="CREAR_MARCA", tabla_afectada="marcas", registro_id=record_id, valor_nuevo={"nombre": nombre, "estado": estado})
        return record_id


def update_brand(cliente_id: int, user_id: int, record_id: int, data: dict):
    nombre = (data.get("nombre") or "").strip()
    descripcion = (data.get("descripcion") or "").strip() or None
    if not nombre:
        raise ValueError("El nombre de la marca es obligatorio.")
    with db_transaction() as (cursor, _connection):
        estado = estado_value(cursor, "marcas", data.get("estado") or "ACTIVO")
        cursor.execute("SELECT * FROM marcas WHERE cliente_id=%s AND id=%s FOR UPDATE", (cliente_id, record_id))
        previous = cursor.fetchone()
        if not previous:
            raise ValueError("Marca no encontrada.")
        cursor.execute("UPDATE marcas SET nombre=%s, descripcion=%s, estado=%s, updated_at=NOW() WHERE id=%s", (nombre, descripcion, estado, record_id))
        log_audit(cursor, cliente_id=cliente_id, usuario_id=user_id, modulo="CATALOGO", accion="EDITAR_MARCA", tabla_afectada="marcas", registro_id=record_id, valor_anterior=previous, valor_nuevo={"nombre": nombre, "estado": estado})
