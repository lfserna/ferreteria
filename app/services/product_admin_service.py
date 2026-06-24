import re

from mysql.connector.errors import IntegrityError

from app.database import db_cursor, db_transaction
from app.services.audit_service import log_audit
from app.services.category_admin_service import estado_value


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
            LEFT JOIN producto_presentaciones pp ON pp.producto_id = p.id AND pp.tipo_presentacion='UNIDAD'
            LEFT JOIN producto_precios pr ON pr.id=(SELECT pr2.id FROM producto_precios pr2 WHERE pr2.producto_presentacion_id=pp.id ORDER BY pr2.id DESC LIMIT 1)
            LEFT JOIN (SELECT cliente_id, producto_id, SUM(cantidad_disponible) AS stock_total FROM inventarios GROUP BY cliente_id, producto_id) stock ON stock.cliente_id = p.cliente_id AND stock.producto_id = p.id
            WHERE p.cliente_id=%s AND p.deleted_at IS NULL
              AND (p.nombre LIKE %s OR COALESCE(p.descripcion,'') LIKE %s OR COALESCE(p.codigo_producto,'') LIKE %s OR COALESCE(p.codigo_barras,'') LIKE %s)
            ORDER BY p.created_at DESC, p.id DESC
            LIMIT 200
            """,
            (cliente_id, search, search, search, search),
        )
        return cursor.fetchall()


def list_stock_locations(cliente_id: int):
    with db_cursor() as cursor:
        cursor.execute("SELECT id,nombre,tipo_ubicacion FROM ubicaciones_stock WHERE cliente_id=%s ORDER BY tipo_ubicacion,nombre", (cliente_id,))
        return cursor.fetchall()


def get_product(cliente_id: int, product_id: int):
    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT p.*, pp.id AS presentacion_id, pr.precio_venta_estandar, pr.precio_minimo_venta
            FROM productos p
            LEFT JOIN producto_presentaciones pp ON pp.producto_id=p.id AND pp.tipo_presentacion='UNIDAD'
            LEFT JOIN producto_precios pr ON pr.id=(SELECT pr2.id FROM producto_precios pr2 WHERE pr2.producto_presentacion_id=pp.id ORDER BY pr2.id DESC LIMIT 1)
            WHERE p.cliente_id=%s AND p.id=%s AND p.deleted_at IS NULL
            LIMIT 1
            """,
            (cliente_id, product_id),
        )
        return cursor.fetchone()


def normalize_barcode(value: str) -> str:
    cleaned = re.sub(r"\s+", "", (value or "").strip())
    if cleaned.isdigit() and len(cleaned) == 13 and cleaned.startswith("0"):
        return cleaned[1:]
    return cleaned


def barcode_aliases(value: str):
    normalized = normalize_barcode(value)
    aliases = {normalized}
    if normalized.isdigit() and len(normalized) == 12:
        aliases.add("0" + normalized)
    if normalized.isdigit() and len(normalized) == 13 and normalized.startswith("0"):
        aliases.add(normalized[1:])
    return aliases


def parse_codes(raw_codes: str):
    values = [normalize_barcode(x) for x in re.split(r"[\n,;]+", raw_codes or "") if normalize_barcode(x)]
    seen = {}
    result = []
    for value in values:
        aliases = barcode_aliases(value)
        duplicate = next((seen[a] for a in aliases if a in seen), None)
        if duplicate:
            raise ValueError(f"Código repetido en el formulario: {value} equivale a {duplicate}")
        for alias in aliases:
            seen[alias] = value
        result.append(value)
    return result


def code_type(data: dict):
    value = (data.get("tipo_codigo_individual") or "BARRAS").strip().upper()
    return value if value in {"INTERNO", "BARRAS", "SERIE"} else "BARRAS"


def create_product(cliente_id: int, user_id: int, data: dict):
    nombre = (data.get("nombre") or "").strip()
    codigo = (data.get("codigo_producto") or "").strip() or None
    codigo_barras = (data.get("codigo_barras") or "").strip() or None
    categoria_id = int(data.get("categoria_id") or 0)
    if not nombre or not categoria_id:
        raise ValueError("Nombre y categoría son obligatorios.")

    codes = parse_codes(data.get("codigos_individuales") or "")
    initial_qty = int(data.get("cantidad_inicial") or 0)
    location_id = int(data.get("ubicacion_stock_id") or 0) or None
    tipo_codigo = code_type(data)

    if codes and initial_qty and len(codes) > initial_qty:
        raise ValueError("No puedes registrar más códigos individuales que cantidad inicial.")
    if initial_qty and not location_id:
        raise ValueError("Selecciona una ubicación para la cantidad inicial.")

    if codes:
        ensure_product_codes_table_once()

    skipped_codes = []
    registered_codes = 0

    with db_transaction() as (cursor, _connection):
        producto_activo = estado_value(cursor, "productos", "ACTIVO")
        cursor.execute(
            """
            INSERT INTO productos
                (cliente_id, categoria_id, marca_id, nombre, descripcion, codigo_producto, codigo_barras,
                 precio_compra, unidad_base, contenido_por_caja, maneja_stock, estado, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'UNIDAD',%s,1,%s,NOW(),NOW())
            """,
            (cliente_id, categoria_id, data.get("marca_id") or None, nombre, data.get("descripcion") or None,
             codigo, codigo_barras, data.get("precio_compra") or None,
             data.get("contenido_por_caja") or None, producto_activo),
        )
        product_id = cursor.lastrowid

        if data.get("precio_venta_estandar") and data.get("precio_minimo_venta"):
            upsert_unit_price(cursor, cliente_id, product_id, data["precio_venta_estandar"], data["precio_minimo_venta"])
        if initial_qty:
            add_initial_stock(cursor, cliente_id, product_id, location_id, initial_qty, user_id)
        if codes:
            result = insert_product_codes(cursor, cliente_id, product_id, location_id, codes, tipo_codigo)
            registered_codes = result["registered"]
            skipped_codes = result["skipped"]

        log_audit(
            cursor,
            cliente_id=cliente_id,
            usuario_id=user_id,
            modulo="CATALOGO",
            accion="CREAR_PRODUCTO",
            tabla_afectada="productos",
            registro_id=product_id,
            valor_nuevo={
                "nombre": nombre,
                "codigo": codigo,
                "codigo_barras": codigo_barras,
                "cantidad_inicial": initial_qty,
                "codigos_recibidos": len(codes),
                "codigos_registrados": registered_codes,
                "codigos_omitidos": skipped_codes,
                "tipo_codigo": tipo_codigo,
            },
        )

    return {
        "product_id": product_id,
        "codes_received": len(codes),
        "codes_registered": registered_codes,
        "codes_skipped": skipped_codes,
    }


def update_product(cliente_id: int, user_id: int, product_id: int, data: dict):
    nombre = (data.get("nombre") or "").strip()
    codigo = (data.get("codigo_producto") or "").strip() or None
    categoria_id = int(data.get("categoria_id") or 0)
    estado = data.get("estado") or "ACTIVO"
    if not nombre or not categoria_id:
        raise ValueError("Nombre y categoría son obligatorios.")

    codes = parse_codes(data.get("codigos_individuales") or "")
    if codes:
        ensure_product_codes_table_once()

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
             (data.get("codigo_barras") or "").strip() or None, data.get("precio_compra") or None,
             data.get("contenido_por_caja") or None, estado, product_id),
        )
        if data.get("precio_venta_estandar") and data.get("precio_minimo_venta"):
            upsert_unit_price(cursor, cliente_id, product_id, data["precio_venta_estandar"], data["precio_minimo_venta"])

        registered_codes = 0
        skipped_codes = []
        if codes:
            result = insert_product_codes(cursor, cliente_id, product_id, None, codes, code_type(data))
            registered_codes = result["registered"]
            skipped_codes = result["skipped"]

        log_audit(cursor, cliente_id=cliente_id, usuario_id=user_id, modulo="CATALOGO", accion="EDITAR_PRODUCTO", tabla_afectada="productos", registro_id=product_id, valor_anterior=previous, valor_nuevo={"nombre": nombre, "estado": estado, "codigos_registrados": registered_codes, "codigos_omitidos": skipped_codes})


def add_initial_stock(cursor, cliente_id, product_id, location_id, amount, user_id):
    cursor.execute("SELECT id FROM inventarios WHERE cliente_id=%s AND producto_id=%s AND ubicacion_stock_id=%s LIMIT 1", (cliente_id, product_id, location_id))
    row = cursor.fetchone()
    if row:
        inv_id = row["id"]
        cursor.execute("UPDATE inventarios SET cantidad_disponible=cantidad_disponible+%s,updated_at=NOW() WHERE id=%s", (amount, inv_id))
    else:
        cursor.execute("INSERT INTO inventarios (cliente_id,producto_id,ubicacion_stock_id,cantidad_disponible,cantidad_reservada,cantidad_minima,updated_at) VALUES (%s,%s,%s,%s,0,0,NOW())", (cliente_id, product_id, location_id, amount))
        inv_id = cursor.lastrowid
    cursor.execute("INSERT INTO inventario_movimientos (cliente_id,producto_id,ubicacion_destino_id,tipo_movimiento,cantidad,referencia_tipo,referencia_id,usuario_id,observacion,created_at) VALUES (%s,%s,%s,'ENTRADA',%s,'PRODUCTO',%s,%s,'Stock inicial al crear producto',NOW())", (cliente_id, product_id, location_id, amount, product_id, user_id))
    return inv_id


def ensure_product_codes_table_once():
    with db_cursor(commit=True) as cursor:
        ensure_product_codes_table(cursor)


def ensure_product_codes_table(cursor):
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS producto_codigos (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            cliente_id BIGINT UNSIGNED NOT NULL,
            producto_id BIGINT UNSIGNED NOT NULL,
            ubicacion_stock_id BIGINT UNSIGNED NULL,
            codigo VARCHAR(120) NOT NULL,
            tipo_codigo ENUM('INTERNO','BARRAS','SERIE') NOT NULL DEFAULT 'INTERNO',
            estado ENUM('DISPONIBLE','VENDIDO','INACTIVO') NOT NULL DEFAULT 'DISPONIBLE',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NULL,
            UNIQUE KEY uq_producto_codigo_individual_cliente (cliente_id, codigo),
            KEY idx_producto_codigos_producto (producto_id),
            KEY idx_producto_codigos_ubicacion (ubicacion_stock_id)
        ) ENGINE=InnoDB
        """
    )


def insert_product_codes(cursor, cliente_id, product_id, location_id, codes, tipo_codigo="BARRAS"):
    estado = estado_value(cursor, "producto_codigos", "DISPONIBLE")
    registered = 0
    skipped = []

    for code in codes:
        normalized = normalize_barcode(code)
        aliases = sorted(barcode_aliases(normalized))
        placeholders = ",".join(["%s"] * len(aliases))
        cursor.execute(
            f"SELECT codigo FROM producto_codigos WHERE cliente_id=%s AND codigo IN ({placeholders}) LIMIT 1",
            tuple([cliente_id] + aliases),
        )
        existing = cursor.fetchone()
        if existing:
            skipped.append(normalized)
            continue

        try:
            cursor.execute(
                """
                INSERT IGNORE INTO producto_codigos
                    (cliente_id,producto_id,ubicacion_stock_id,codigo,tipo_codigo,estado,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,NOW())
                """,
                (cliente_id, product_id, location_id, normalized, tipo_codigo, estado),
            )
            if cursor.rowcount == 1:
                registered += 1
            else:
                skipped.append(normalized)
        except IntegrityError:
            skipped.append(normalized)

    return {"registered": registered, "skipped": skipped}


def upsert_unit_price(cursor, cliente_id: int, product_id: int, precio_venta, precio_minimo):
    if float(precio_minimo) > float(precio_venta):
        raise ValueError("El precio mínimo no puede ser mayor al precio estándar.")
    presentacion_activa = estado_value(cursor, "producto_presentaciones", "ACTIVO")
    precio_activo = estado_value(cursor, "producto_precios", "ACTIVO")
    precio_inactivo = estado_value(cursor, "producto_precios", "INACTIVO")
    cursor.execute("SELECT id FROM producto_presentaciones WHERE cliente_id=%s AND producto_id=%s AND tipo_presentacion='UNIDAD' LIMIT 1", (cliente_id, product_id))
    row = cursor.fetchone()
    if row:
        presentation_id = row["id"]
        cursor.execute("UPDATE producto_presentaciones SET nombre='Unidad',factor_unidad_base=1,estado=%s,updated_at=NOW() WHERE id=%s", (presentacion_activa, presentation_id))
    else:
        cursor.execute("INSERT INTO producto_presentaciones (cliente_id, producto_id, tipo_presentacion, nombre, factor_unidad_base, estado, created_at, updated_at) VALUES (%s,%s,'UNIDAD','Unidad',1,%s,NOW(),NOW())", (cliente_id, product_id, presentacion_activa))
        presentation_id = cursor.lastrowid
    cursor.execute("UPDATE producto_precios SET estado=%s, updated_at=NOW() WHERE cliente_id=%s AND producto_presentacion_id=%s", (precio_inactivo, cliente_id, presentation_id))
    cursor.execute("INSERT INTO producto_precios (cliente_id, producto_id, producto_presentacion_id, precio_venta_estandar, precio_minimo_venta, moneda, vigente_desde, estado, created_at, updated_at) VALUES (%s,%s,%s,%s,%s,'BOB',NOW(),%s,NOW(),NOW())", (cliente_id, product_id, presentation_id, precio_venta, precio_minimo, precio_activo))
