from app.database import db_cursor


def get_user_context(user_id: int):
    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT u.id, u.cliente_id, u.sucursal_id, u.username, u.nombres,
                   u.apellido_paterno, u.apellido_materno, c.nombre_comercial AS cliente_nombre,
                   s.nombre AS sucursal_nombre, ur.rol_id, r.codigo AS rol_codigo,
                   r.nombre AS rol_nombre, ur.almacen_id, a.nombre AS almacen_nombre
            FROM usuarios u
            JOIN clientes c ON c.id = u.cliente_id
            LEFT JOIN sucursales s ON s.id = u.sucursal_id
            LEFT JOIN usuario_roles ur ON ur.usuario_id = u.id AND ur.estado = 'ACTIVO'
            LEFT JOIN roles r ON r.id = ur.rol_id
            LEFT JOIN almacenes a ON a.id = ur.almacen_id
            WHERE u.id = %s AND u.estado = 'ACTIVO'
            ORDER BY ur.id ASC
            LIMIT 1
            """,
            (user_id,),
        )
        return cursor.fetchone()


def get_primary_stock_location(cliente_id: int, sucursal_id: int | None, almacen_id: int | None = None):
    with db_cursor() as cursor:
        if almacen_id:
            cursor.execute(
                "SELECT id FROM ubicaciones_stock WHERE cliente_id = %s AND almacen_id = %s AND estado = 'ACTIVO' LIMIT 1",
                (cliente_id, almacen_id),
            )
            row = cursor.fetchone()
            if row:
                return row["id"]
        if sucursal_id:
            cursor.execute(
                """
                SELECT id FROM ubicaciones_stock
                WHERE cliente_id = %s AND sucursal_id = %s AND tipo_ubicacion = 'SUCURSAL' AND estado = 'ACTIVO'
                LIMIT 1
                """,
                (cliente_id, sucursal_id),
            )
            row = cursor.fetchone()
            if row:
                return row["id"]
    return None
