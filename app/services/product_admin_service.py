from app.database import db_cursor, db_transaction
from app.services.audit_service import log_audit


def list_products_admin(cliente_id: int, query: str = ""):
    search = f"%{query.strip()}%"
    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT p.id, p.nombre, p.descripcion, p.codigo_producto, p.codigo_barras,
                   p.precio_compra, p.contenido_por_caja, p.estado, c.nombre AS categoria,
                   m.nombre AS marca, pr.precio_venta_estandar, pr.precio_minimo_venta,
                   COALESCE(stock.stock_total, 0) AS stock_total
            FROM productos p
            JOIN categorias_producto c ON c.id = p.categoria_id
            LEFT JOIN marcas m ON m.id = p.marca_id
            LEFT JOIN producto_presentaciones pp ON pp.producto_id = p.id AND pp.tipo_presentacion='UNIDAD' AND pp.estado='ACTIVO'
            LEFT JOIN producto_precios pr ON pr.producto_presentacion_id = pp.id AND pr.estado='ACTIVO'
            LEFT JOIN (
                SELECT cliente_id, producto_id, SUM(cantidad_disponible) AS stock_total
                FROM inventarios GROUP BY cliente_id, producto_id
            ) stock ON stock.cliente_id = p.cliente_id AND stock.producto_id = p.id
            WHERE p.cliente_id=%s AND p.deleted_at IS NULL
              AND (p.nombre LIKE %s OR COALESCE(p.descripcion,'') LIKE %s OR p.codigo_producto LIKE %s OR COALESCE(p.codigo_barras,'') LIKE %s)
            ORDER BY p.created_at DESC, p.id DESC
            LIMIT 200
            """,
            (cliente_id, search, search, search, search),
        )
        return cursor.fetchall()


def get_product(cliente_id: int, product_id: int):
    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT p.*, pp.id AS presentacion_id, pr.precio_venta_estandar, pr.precio_minimo_venta
            FROM productos p
            LEFT JOIN producto_presentaciones pp ON pp.producto_id=p.id AND pp.tipo_presentacion='UNIDAD' AND pp.estado='ACTIVO'
            LEFT JOIN producto_precios pr ON pr.producto_presentacion_id=pp.id AND pr.estado='ACTIVO'
            WHERE p.cliente_id=%s AND p.id=%s AND p.deleted_at IS NULL
            LIMIT 1
            """,
            (cliente_id, product_id),
        )
        return cursor.fetchone()


def create_product(cliente_id: int, user_id: int, data: dict):
    nombre = (data.get("nombre") or "").strip()
    codigo = (data.get("codigo_producto") or "").strip()
    categoria_id = int(data.get("categoria_id") or 0)
    if not nombre or not codigo or not categoria_id:
        raise ValueError("Nombre, código y categoría son obligatorios.")
    with db_transaction() as (cursor, _connection):
        cursor.execute(
            """
            INSERT INTO productos
                (cliente_id, categoria_id, marca_id, nombre, descripcion, codigo_producto, codigo_barras,
                 precio_compra, unidad_base, contenido_por_caja, maneja_stock, estado, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'UNIDAD',%s,1,'ACTIVO',NOW(),NOW())
            """,
            (cliente_id, categoria_id, data.get("marca_id") or None, nombre, data.get("descripcion") or None,
             codigo, data.get("codigo_barras") or None, data.get("precio_compra") or None,
             data.get("contenido_por_caja") or None),
        )
        product_id = cursor.lastrowid
        if data.get("precio_venta_estandar") and data.get("precio_minimo_venta"):
            upsert_unit_price(cursor, cliente_id, product_id, data["precio_venta_estandar"], data["precio_minimo_venta"])
        log_audit(cursor, cliente_id=cliente_id, usuario_id=user_id, modulo="CATALOGO", accion="CREAR_PRODUCTO", tabla_afectada="productos", registro_id=product_id, valor_nuevo={"nombre": nombre, "codigo": codigo})
        return product_id


def update_product(cliente_id: int, user_id: int, product_id: int, data: dict):
    nombre = (data.get("nombre") or "").strip()
    codigo = (data.get("codigo_producto") or "").strip()
    categoria_id = int(data.get("categoria_id") or 0)
    estado = data.get("estado") or "ACTIVO"
    if not nombre or not codigo or not categoria_id:
        raise ValueError("Nombre, código y categoría son obligatorios.")
    with db_transaction() as (cursor, _connection):
        cursor.execute("SELECT * FROM productos WHERE cliente_id=%s AND id=%s FOR UPDATE", (cliente_id, product_id))
        previous = cursor.fetchone()
        if not previous:
            raise ValueError("Producto no encontrado.")
        cursor.execute(
            """
            UPDATE productos
            SET categoria_id=%s, marca_id=%s, nombre=%s, descripcion=%s, codigo_producto=%s,
                codigo_barras=%s, precio_compra=%s, contenido_por_caja=%s, estado=%s, updated_at=NOW()
            WHERE id=%s
            """,
            (categoria_id, data.get("marca_id") or None, nombre, data.get("descripcion") or None, codigo,
             data.get("codigo_barras") or None, data.get("precio_compra") or None,
             data.get("contenido_por_caja") or None, estado, product_id),
        )
        if data.get("precio_venta_estandar") and data.get("precio_minimo_venta"):
            upsert_unit_price(cursor, cliente_id, product_id, data["precio_venta_estandar"], data["precio_minimo_venta"])
        log_audit(cursor, cliente_id=cliente_id, usuario_id=user_id, modulo="CATALOGO", accion="EDITAR_PRODUCTO", tabla_afectada="productos", registro_id=product_id, valor_anterior=previous, valor_nuevo={"nombre": nombre, "estado": estado})


def upsert_unit_price(cursor, cliente_id: int, product_id: int, precio_venta, precio_minimo):
    if float(precio_minimo) > float(precio_venta):
        raise ValueError("El precio mínimo no puede ser mayor al precio estándar.")
    cursor.execute(
        "SELECT id FROM producto_presentaciones WHERE cliente_id=%s AND producto_id=%s AND tipo_presentacion='UNIDAD' LIMIT 1",
        (cliente_id, product_id),
    )
    row = cursor.fetchone()
    if row:
        presentation_id = row["id"]
    else:
        cursor.execute(
            "INSERT INTO producto_presentaciones (cliente_id, producto_id, tipo_presentacion, nombre, factor_unidad_base, estado, created_at, updated_at) VALUES (%s,%s,'UNIDAD','Unidad',1,'ACTIVO',NOW(),NOW())",
            (cliente_id, product_id),
        )
        presentation_id = cursor.lastrowid
    cursor.execute(
        "UPDATE producto_precios SET estado='INACTIVO', updated_at=NOW() WHERE cliente_id=%s AND producto_presentacion_id=%s AND estado='ACTIVO'",
        (cliente_id, presentation_id),
    )
    cursor.execute(
        """
        INSERT INTO producto_precios
            (cliente_id, producto_id, producto_presentacion_id, precio_venta_estandar, precio_minimo_venta, moneda, vigente_desde, estado, created_at, updated_at)
        VALUES (%s,%s,%s,%s,%s,'BOB',NOW(),'ACTIVO',NOW(),NOW())
        """,
        (cliente_id, product_id, presentation_id, precio_venta, precio_minimo),
    )
