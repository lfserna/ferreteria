from app.database import db_cursor


def search_products(cliente_id: int, query: str = "", limit: int = 30, ubicacion_stock_id: int | None = None):
    search = f"%{query.strip()}%"
    local_location = ubicacion_stock_id or 0
    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT p.id AS producto_id,
                   pp.id AS presentacion_id,
                   p.nombre,
                   p.descripcion,
                   p.codigo_producto,
                   p.codigo_barras,
                   c.nombre AS categoria,
                   COALESCE(pp.nombre, 'Unidad') AS presentacion,
                   COALESCE(pp.tipo_presentacion, 'UNIDAD') AS tipo_presentacion,
                   COALESCE(pp.factor_unidad_base, 1) AS factor_unidad_base,
                   pr.precio_venta_estandar,
                   pr.precio_minimo_venta,
                   CASE WHEN pp.id IS NOT NULL AND pr.id IS NOT NULL AND pr.precio_venta_estandar > 0 THEN 1 ELSE 0 END AS vendible,
                   COALESCE(stock.stock_total, 0) AS stock_total,
                   CASE WHEN %s = 0 THEN COALESCE(stock.stock_total,0) ELSE COALESCE(stock.stock_local, 0) END AS stock_local,
                   COALESCE(stock.stock_ubicaciones, 'Sin stock registrado') AS stock_ubicaciones,
                   COALESCE(stock.stock_otras_ubicaciones, 'Sin disponibilidad en otras ubicaciones') AS stock_otras_ubicaciones
            FROM productos p
            JOIN categorias_producto c ON c.id = p.categoria_id
            LEFT JOIN producto_presentaciones pp ON pp.id=(
                SELECT pp2.id
                FROM producto_presentaciones pp2
                WHERE pp2.producto_id=p.id
                ORDER BY CASE WHEN pp2.tipo_presentacion='UNIDAD' THEN 0 ELSE 1 END, pp2.id ASC
                LIMIT 1
            )
            LEFT JOIN producto_precios pr ON pr.id=(
                SELECT pr2.id
                FROM producto_precios pr2
                WHERE pr2.producto_presentacion_id=pp.id
                ORDER BY CASE WHEN pr2.estado='ACTIVO' THEN 0 ELSE 1 END, pr2.id DESC
                LIMIT 1
            )
            LEFT JOIN (
                SELECT i.cliente_id,
                       i.producto_id,
                       SUM(i.cantidad_disponible) AS stock_total,
                       SUM(CASE WHEN i.ubicacion_stock_id=%s THEN i.cantidad_disponible ELSE 0 END) AS stock_local,
                       GROUP_CONCAT(
                         DISTINCT CONCAT(u.nombre, ': ', CAST(i.cantidad_disponible AS UNSIGNED))
                         ORDER BY u.tipo_ubicacion, u.nombre SEPARATOR ' | '
                       ) AS stock_ubicaciones,
                       GROUP_CONCAT(
                         DISTINCT CASE WHEN i.ubicacion_stock_id<>%s THEN CONCAT(u.nombre, ': ', CAST(i.cantidad_disponible AS UNSIGNED)) ELSE NULL END
                         ORDER BY u.tipo_ubicacion, u.nombre SEPARATOR ' | '
                       ) AS stock_otras_ubicaciones
                FROM inventarios i
                JOIN ubicaciones_stock u ON u.id = i.ubicacion_stock_id
                GROUP BY i.cliente_id, i.producto_id
            ) stock ON stock.cliente_id = p.cliente_id AND stock.producto_id = p.id
            WHERE p.cliente_id = %s
              AND p.estado = 'ACTIVO'
              AND p.deleted_at IS NULL
              AND (
                    p.nombre LIKE %s
                 OR COALESCE(p.descripcion, '') LIKE %s
                 OR COALESCE(p.codigo_producto, '') LIKE %s
                 OR COALESCE(p.codigo_barras, '') LIKE %s
              )
            ORDER BY p.nombre ASC
            LIMIT %s
            """,
            (local_location, local_location, local_location, cliente_id, search, search, search, search, limit),
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
                   CAST(i.cantidad_disponible AS UNSIGNED) AS cantidad_disponible,
                   CAST(i.cantidad_reservada AS UNSIGNED) AS cantidad_reservada,
                   CAST(i.cantidad_minima AS UNSIGNED) AS cantidad_minima
            FROM inventarios i
            JOIN productos p ON p.id = i.producto_id
            JOIN ubicaciones_stock u ON u.id = i.ubicacion_stock_id
            LEFT JOIN sucursales s ON s.id = u.sucursal_id
            LEFT JOIN almacenes a ON a.id = u.almacen_id
            WHERE i.cliente_id = %s
              AND (p.nombre LIKE %s OR COALESCE(p.descripcion, '') LIKE %s OR COALESCE(p.codigo_producto, '') LIKE %s OR COALESCE(p.codigo_barras, '') LIKE %s OR u.nombre LIKE %s)
            ORDER BY p.nombre, u.tipo_ubicacion, u.nombre
            LIMIT 300
            """,
            (cliente_id, search, search, search, search, search),
        )
        return cursor.fetchall()
