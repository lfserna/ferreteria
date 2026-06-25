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


def _find_stock_location(cursor, cliente_id: int, field_name: str, location_id: int, tipo: str | None = None):
    type_filter = "AND tipo_ubicacion = %s" if tipo else ""
    params = [cliente_id, location_id]
    if tipo:
        params.append(tipo)
    cursor.execute(
        f"""
        SELECT id
        FROM ubicaciones_stock
        WHERE cliente_id = %s
          AND {field_name} = %s
          {type_filter}
          AND (estado = 'ACTIVO' OR estado IS NULL)
        ORDER BY id ASC
        LIMIT 1
        """,
        tuple(params),
    )
    row = cursor.fetchone()
    if row:
        return row["id"]

    params = [cliente_id, location_id]
    if tipo:
        params.append(tipo)
    cursor.execute(
        f"""
        SELECT id
        FROM ubicaciones_stock
        WHERE cliente_id = %s
          AND {field_name} = %s
          {type_filter}
        ORDER BY id ASC
        LIMIT 1
        """,
        tuple(params),
    )
    row = cursor.fetchone()
    return row["id"] if row else None


def _create_sucursal_stock_location(cursor, cliente_id: int, sucursal_id: int):
    cursor.execute("SELECT nombre FROM sucursales WHERE cliente_id=%s AND id=%s LIMIT 1", (cliente_id, sucursal_id))
    sucursal = cursor.fetchone()
    if not sucursal:
        return None
    cursor.execute(
        """
        INSERT INTO ubicaciones_stock
            (cliente_id, tipo_ubicacion, sucursal_id, almacen_id, nombre, estado, created_at, updated_at)
        VALUES (%s, 'SUCURSAL', %s, NULL, %s, 'ACTIVO', NOW(), NOW())
        """,
        (cliente_id, sucursal_id, sucursal["nombre"]),
    )
    return cursor.lastrowid


def _create_almacen_stock_location(cursor, cliente_id: int, almacen_id: int):
    cursor.execute("SELECT nombre FROM almacenes WHERE cliente_id=%s AND id=%s LIMIT 1", (cliente_id, almacen_id))
    almacen = cursor.fetchone()
    if not almacen:
        return None
    cursor.execute(
        """
        INSERT INTO ubicaciones_stock
            (cliente_id, tipo_ubicacion, sucursal_id, almacen_id, nombre, estado, created_at, updated_at)
        VALUES (%s, 'ALMACEN', NULL, %s, %s, 'ACTIVO', NOW(), NOW())
        """,
        (cliente_id, almacen_id, almacen["nombre"]),
    )
    return cursor.lastrowid


def get_primary_stock_location(cliente_id: int, sucursal_id: int | None, almacen_id: int | None = None):
    with db_cursor(commit=True) as cursor:
        if almacen_id:
            location_id = _find_stock_location(cursor, cliente_id, "almacen_id", almacen_id, "ALMACEN")
            if location_id:
                return location_id
            location_id = _find_stock_location(cursor, cliente_id, "almacen_id", almacen_id)
            if location_id:
                return location_id
            return _create_almacen_stock_location(cursor, cliente_id, almacen_id)

        if sucursal_id:
            location_id = _find_stock_location(cursor, cliente_id, "sucursal_id", sucursal_id, "SUCURSAL")
            if location_id:
                return location_id
            location_id = _find_stock_location(cursor, cliente_id, "sucursal_id", sucursal_id)
            if location_id:
                return location_id
            return _create_sucursal_stock_location(cursor, cliente_id, sucursal_id)

    return None
