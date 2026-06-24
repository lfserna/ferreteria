from app.database import db_cursor


def search_products(cliente_id: int, query: str = "", limit: int = 30):
    search = f"%{query.strip()}%"
    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT p.id AS producto_id, pp.id AS presentacion_id, p.nombre, p.descripcion,
                   p.codigo_producto, p.codigo_barras, c.nombre AS categoria,
                   pp.nombre AS presentacion, pp.tipo_presentacion, pp.factor_unidad_base,
                   pr.precio_venta_estandar, pr.precio_minimo_venta,
                   COALESCE(SUM(i.cantidad_disponible), 0) AS stock_total,
                   COALESCE(
                     GROUP_CONCAT(
                       DISTINCT CONCAT(u.nombre, ': ', TRIM(TRAILING '.000' FROM CAST(i.cantidad_disponible AS CHAR)))
                       ORDER BY u.tipo_ubicacion, u.nombre SEPARATOR ' | '
                     ),
                     'Sin stock registrado'
                   ) AS stock_ubicaciones
            FROM productos p
            JOIN categorias_producto c ON c.id = p.categoria_id
            JOIN producto_presentaciones pp ON pp.producto_id = p.id AND pp.estado = 'ACTIVO'
            JOIN producto_precios pr ON pr.producto_presentacion_id = pp.id AND pr.estado = 'ACTIVO'
            LEFT JOIN inventarios i ON i.producto_id = p.id AND i.cliente_id = p.cliente_id
            LEFT JOIN ubicaciones_stock u ON u.id = i.ubicacion_stock_id
            WHERE p.cliente_id = %s AND p.estado = 'ACTIVO'
              AND (p.nombre LIKE %s OR p.descripcion LIKE %s OR p.codigo_producto LIKE %s OR p.codigo_barras LIKE %s)
            GROUP BY p.id, pp.id, pr.id, c.nombre
            ORDER BY p.nombre ASC, pp.factor_unidad_base ASC
            LIMIT %s
            """,
            (cliente_id, search, search, search, search, limit),
        )
        return cursor.fetchall()


def list_categories(cliente_id: int):
    with db_cursor() as cursor:
        cursor.execute(
            "SELECT id, nombre, descripcion, estado FROM categorias_producto WHERE cliente_id = %s AND deleted_at IS NULL ORDER BY nombre",
            (cliente_id,),
        )
        return cursor.fetchall()


def list_stock(cliente_id: int, query: str = ""):
    search = f"%{query.strip()}%"
    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT p.nombre AS producto, p.codigo_producto, p.codigo_barras, u.nombre AS ubicacion,
                   u.tipo_ubicacion, COALESCE(s.nombre, a.nombre) AS sucursal_o_almacen,
                   i.cantidad_disponible, i.cantidad_reservada, i.cantidad_minima
            FROM inventarios i
            JOIN productos p ON p.id = i.producto_id
            JOIN ubicaciones_stock u ON u.id = i.ubicacion_stock_id
            LEFT JOIN sucursales s ON s.id = u.sucursal_id
            LEFT JOIN almacenes a ON a.id = u.almacen_id
            WHERE i.cliente_id = %s
              AND (p.nombre LIKE %s OR p.descripcion LIKE %s OR p.codigo_producto LIKE %s OR p.codigo_barras LIKE %s OR u.nombre LIKE %s)
            ORDER BY p.nombre, u.tipo_ubicacion, u.nombre
            LIMIT 300
            """,
            (cliente_id, search, search, search, search, search),
        )
        return cursor.fetchall()
