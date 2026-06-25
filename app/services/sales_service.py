import re
from decimal import Decimal

from app.database import db_cursor, db_transaction
from app.services.audit_service import log_audit

PAYMENT_METHODS = {"EFECTIVO", "QR", "TARJETA", "TRANSFERENCIA", "MIXTO"}


def money(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


def whole_units(value, field_name="cantidad"):
    try:
        decimal_value = Decimal(str(value or "0"))
    except Exception as exc:
        raise ValueError(f"La {field_name} debe ser un número entero.") from exc
    if decimal_value <= 0 or decimal_value != decimal_value.to_integral_value():
        raise ValueError(f"La {field_name} debe ser un número entero mayor a cero.")
    return int(decimal_value)


def nonnegative_whole_units(value, field_name="cantidad"):
    try:
        decimal_value = Decimal(str(value or "0"))
    except Exception as exc:
        raise ValueError(f"La {field_name} debe ser un número entero.") from exc
    if decimal_value < 0 or decimal_value != decimal_value.to_integral_value():
        raise ValueError(f"La {field_name} debe ser un número entero mayor o igual a cero.")
    return int(decimal_value)


def column_info(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    return {row["Field"]: row for row in cursor.fetchall()}


def table_columns(cursor, table_name):
    return set(column_info(cursor, table_name).keys())


def index_exists(cursor, table_name, index_name):
    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND INDEX_NAME = %s
        """,
        (table_name, index_name),
    )
    row = cursor.fetchone() or {}
    return int(row.get("total") or 0) > 0


def enum_options(column_type):
    text = str(column_type or "")
    match = re.match(r"enum\((.*)\)", text, flags=re.IGNORECASE)
    if not match:
        return []
    return [value.replace("''", "'") for value in re.findall(r"'((?:[^']|'')*)'", match.group(1))]


def enum_value_for_column(cursor, table_name, column_name, preferred_values):
    info = column_info(cursor, table_name).get(column_name)
    if not info:
        return None
    options = enum_options(info.get("Type"))
    if not options:
        return preferred_values[0] if preferred_values else None
    normalized = {option.upper(): option for option in options}
    for value in preferred_values:
        if value.upper() in normalized:
            return normalized[value.upper()]
    default_value = info.get("Default")
    if default_value in options:
        return default_value
    return options[0] if options else None


def ensure_sales_schema():
    with db_cursor(commit=True) as cursor:
        columns = table_columns(cursor, "ventas")
        if "idempotency_key" not in columns:
            cursor.execute("ALTER TABLE ventas ADD COLUMN idempotency_key VARCHAR(120) NULL")
            columns.add("idempotency_key")
        if not index_exists(cursor, "ventas", "idx_ventas_idempotency"):
            cursor.execute("ALTER TABLE ventas ADD INDEX idx_ventas_idempotency (cliente_id, idempotency_key)")
    return True


def list_pending_orders(cliente_id: int, sucursal_id: int | None):
    with db_cursor() as cursor:
        params = [cliente_id]
        where_sucursal = ""
        if sucursal_id:
            where_sucursal = "AND ov.sucursal_id = %s"
            params.append(sucursal_id)
        cursor.execute(
            f"""
            SELECT ov.id, ov.codigo_orden, ov.subtotal, ov.descuento_total, ov.total_estimado,
                   ov.created_at, u.nombres AS vendedor_nombre, COUNT(od.id) AS items
            FROM ordenes_venta ov
            LEFT JOIN usuarios u ON u.id = ov.vendedor_id
            LEFT JOIN orden_venta_detalles od ON od.orden_venta_id = ov.id
            WHERE ov.cliente_id = %s AND ov.estado = 'ENVIADA_CAJA' {where_sucursal}
            GROUP BY ov.id, u.nombres
            ORDER BY ov.created_at ASC
            LIMIT 25
            """,
            tuple(params),
        )
        return cursor.fetchall()


def get_order(cliente_id: int, orden_id: int):
    with db_cursor() as cursor:
        cursor.execute("SELECT * FROM ordenes_venta WHERE cliente_id=%s AND id=%s LIMIT 1", (cliente_id, orden_id))
        order = cursor.fetchone()
        if not order:
            return None
        cursor.execute(
            """
            SELECT od.*, p.nombre, p.codigo_producto, pp.nombre AS presentacion
            FROM orden_venta_detalles od
            JOIN productos p ON p.id = od.producto_id
            JOIN producto_presentaciones pp ON pp.id = od.producto_presentacion_id
            WHERE od.orden_venta_id = %s
            ORDER BY od.id
            """,
            (orden_id,),
        )
        order["detalles"] = cursor.fetchall()
        return order


def prepare_items(cursor, cliente_id, items):
    prepared = []
    for raw in items:
        producto_id = int(raw["producto_id"])
        presentacion_id = int(raw["presentacion_id"])
        cantidad = whole_units(raw.get("cantidad", 1))
        precio_unitario = money(raw.get("precio_unitario"))
        cursor.execute(
            """
            SELECT p.id AS producto_id, p.nombre AS producto_nombre, pp.id AS presentacion_id,
                   pp.nombre AS presentacion_nombre, COALESCE(pp.factor_unidad_base, 1) AS factor_unidad_base,
                   pr.precio_venta_estandar, pr.precio_minimo_venta
            FROM productos p
            JOIN producto_presentaciones pp ON pp.producto_id = p.id AND pp.id = %s
            LEFT JOIN producto_precios pr ON pr.id=(
                SELECT pr2.id
                FROM producto_precios pr2
                WHERE pr2.producto_presentacion_id=pp.id
                ORDER BY CASE WHEN pr2.estado='ACTIVO' THEN 0 ELSE 1 END, pr2.id DESC
                LIMIT 1
            )
            WHERE p.cliente_id = %s AND p.id = %s AND p.estado = 'ACTIVO'
            LIMIT 1
            """,
            (presentacion_id, cliente_id, producto_id),
        )
        product = cursor.fetchone()
        if not product or product.get("precio_venta_estandar") is None or product.get("precio_minimo_venta") is None:
            raise ValueError("Producto inválido o sin precio de venta configurado.")
        factor = whole_units(product["factor_unidad_base"], "factor de presentación")
        precio_estandar = money(product["precio_venta_estandar"])
        precio_minimo = money(product["precio_minimo_venta"])
        if precio_unitario <= 0:
            precio_unitario = precio_estandar
        if precio_unitario < precio_minimo:
            raise ValueError(f"{product['producto_nombre']} no puede venderse por debajo de {precio_minimo}.")
        if precio_unitario > precio_estandar:
            raise ValueError(f"{product['producto_nombre']} no puede venderse por encima del precio estándar.")
        prepared.append({
            "producto_id": product["producto_id"],
            "producto_nombre": product["producto_nombre"],
            "presentacion_id": product["presentacion_id"],
            "presentacion_nombre": product["presentacion_nombre"],
            "cantidad": cantidad,
            "cantidad_base": cantidad * factor,
            "precio_estandar": precio_estandar,
            "precio_minimo": precio_minimo,
            "precio_unitario": precio_unitario,
        })
    return prepared


def create_order_from_cart(*, cliente_id, sucursal_id, ubicacion_stock_id, vendedor_id, created_by, items):
    if not items:
        raise ValueError("El carrito está vacío.")
    with db_transaction() as (cursor, _connection):
        prepared = prepare_items(cursor, cliente_id, items)
        subtotal = sum(i["precio_estandar"] * i["cantidad"] for i in prepared)
        total = sum(i["precio_unitario"] * i["cantidad"] for i in prepared)
        descuento = subtotal - total
        cursor.execute(
            """
            INSERT INTO ordenes_venta
                (cliente_id, sucursal_id, ubicacion_stock_id, vendedor_id, creado_por_usuario_id,
                 codigo_orden, estado, subtotal, descuento_total, total_estimado, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,CONCAT('ORD-', DATE_FORMAT(NOW(), '%Y%m%d'), '-', UUID_SHORT()),
                    'ENVIADA_CAJA',%s,%s,%s,NOW(),NOW())
            """,
            (cliente_id, sucursal_id, ubicacion_stock_id, vendedor_id, created_by, subtotal, descuento, total),
        )
        order_id = cursor.lastrowid
        insert_order_details(cursor, cliente_id, order_id, prepared)
        log_audit(cursor, cliente_id=cliente_id, usuario_id=created_by, modulo="VENTAS", accion="ENVIAR_ORDEN_CAJA", tabla_afectada="ordenes_venta", registro_id=order_id, valor_nuevo={"total": total})
        return {"order_id": order_id, "total": str(total)}


def insert_payment(cursor, cliente_id, venta_id, metodo_pago, total):
    columns = table_columns(cursor, "venta_pagos")
    estado_value = enum_value_for_column(cursor, "venta_pagos", "estado", ["CONFIRMADO", "PAGADO", "PAGADA", "COMPLETADO", "ACTIVO"])
    if "estado" in columns and estado_value:
        cursor.execute(
            """
            INSERT INTO venta_pagos (cliente_id, venta_id, metodo_pago, monto, estado, created_at)
            VALUES (%s,%s,%s,%s,%s,NOW())
            """,
            (cliente_id, venta_id, metodo_pago, total, estado_value),
        )
    else:
        cursor.execute(
            """
            INSERT INTO venta_pagos (cliente_id, venta_id, metodo_pago, monto, created_at)
            VALUES (%s,%s,%s,%s,NOW())
            """,
            (cliente_id, venta_id, metodo_pago, total),
        )


def confirm_sale_from_cart(*, cliente_id, sucursal_id, ubicacion_stock_id, cajero_id, vendedor_id,
                           created_by, items, metodo_pago, idempotency_key, orden_id=None):
    if metodo_pago not in PAYMENT_METHODS:
        raise ValueError("Método de pago inválido.")
    if not idempotency_key:
        raise ValueError("Falta la llave de idempotencia.")
    ensure_sales_schema()
    with db_transaction() as (cursor, _connection):
        cursor.execute("SELECT id, numero_venta, total FROM ventas WHERE cliente_id=%s AND idempotency_key=%s LIMIT 1", (cliente_id, idempotency_key))
        existing = cursor.fetchone()
        if existing:
            return {"venta_id": existing["id"], "numero_venta": existing["numero_venta"], "total": str(existing["total"]), "duplicada": True}
        if orden_id:
            cursor.execute("SELECT * FROM ordenes_venta WHERE cliente_id=%s AND id=%s AND estado IN ('ENVIADA_CAJA','EN_REVISION_CAJA','APROBADA') FOR UPDATE", (cliente_id, orden_id))
            order = cursor.fetchone()
            if not order:
                raise ValueError("La orden no existe o ya fue procesada.")
            vendedor_id = order["vendedor_id"]
            sucursal_id = order["sucursal_id"]
            ubicacion_stock_id = order["ubicacion_stock_id"]
            prepared = items_from_order(cursor, orden_id)
        else:
            prepared = prepare_items(cursor, cliente_id, items)
            orden_id = create_internal_order(cursor, cliente_id, sucursal_id, ubicacion_stock_id, vendedor_id, created_by, prepared)
        subtotal = sum(i["precio_estandar"] * i["cantidad"] for i in prepared)
        total = sum(i["precio_unitario"] * i["cantidad"] for i in prepared)
        descuento = subtotal - total
        for item in prepared:
            discount_stock(cursor, cliente_id, ubicacion_stock_id, item)
        cursor.execute(
            """
            INSERT INTO ventas
                (cliente_id, sucursal_id, ubicacion_stock_id, orden_venta_id, cajero_id, vendedor_id,
                 numero_venta, fecha_venta, subtotal, descuento_total, total, estado, idempotency_key, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,CONCAT('V-', DATE_FORMAT(NOW(), '%Y%m%d'), '-', UUID_SHORT()),
                    NOW(),%s,%s,%s,'PAGADA',%s,NOW(),NOW())
            """,
            (cliente_id, sucursal_id, ubicacion_stock_id, orden_id, cajero_id, vendedor_id, subtotal, descuento, total, idempotency_key),
        )
        venta_id = cursor.lastrowid
        for item in prepared:
            item_discount = (item["precio_estandar"] - item["precio_unitario"]) * item["cantidad"]
            item_subtotal = item["precio_unitario"] * item["cantidad"]
            cursor.execute(
                """
                INSERT INTO venta_detalles
                    (cliente_id, venta_id, producto_id, producto_presentacion_id, cantidad, precio_estandar,
                     precio_minimo_venta, precio_unitario, descuento, subtotal, created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                """,
                (cliente_id, venta_id, item["producto_id"], item["presentacion_id"], item["cantidad"], item["precio_estandar"], item["precio_minimo"], item["precio_unitario"], item_discount, item_subtotal),
            )
            cursor.execute(
                """
                INSERT INTO inventario_movimientos
                    (cliente_id, producto_id, ubicacion_origen_id, tipo_movimiento, cantidad, referencia_tipo, referencia_id, usuario_id, observacion, created_at)
                VALUES (%s,%s,%s,'VENTA',%s,'VENTA',%s,%s,%s,NOW())
                """,
                (cliente_id, item["producto_id"], ubicacion_stock_id, item["cantidad_base"], venta_id, cajero_id, f"Venta de {item['cantidad']} {item['presentacion_nombre']}"),
            )
        insert_payment(cursor, cliente_id, venta_id, metodo_pago, total)
        cursor.execute("UPDATE ordenes_venta SET estado='FACTURADA', cajero_id=%s, updated_at=NOW() WHERE id=%s", (cajero_id, orden_id))
        log_audit(cursor, cliente_id=cliente_id, usuario_id=created_by, modulo="VENTAS", accion="CONFIRMAR_VENTA", tabla_afectada="ventas", registro_id=venta_id, valor_nuevo={"total": total, "metodo_pago": metodo_pago})
        cursor.execute("SELECT numero_venta FROM ventas WHERE id=%s", (venta_id,))
        return {"venta_id": venta_id, "numero_venta": cursor.fetchone()["numero_venta"], "total": str(total), "duplicada": False}


def insert_order_details(cursor, cliente_id, order_id, prepared):
    for item in prepared:
        item_discount = (item["precio_estandar"] - item["precio_unitario"]) * item["cantidad"]
        cursor.execute(
            """
            INSERT INTO orden_venta_detalles
                (cliente_id, orden_venta_id, producto_id, producto_presentacion_id, cantidad, precio_estandar,
                 precio_minimo_venta, precio_unitario, descuento, subtotal, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
            """,
            (cliente_id, order_id, item["producto_id"], item["presentacion_id"], item["cantidad"], item["precio_estandar"], item["precio_minimo"], item["precio_unitario"], item_discount, item["precio_unitario"] * item["cantidad"]),
        )


def create_internal_order(cursor, cliente_id, sucursal_id, ubicacion_stock_id, vendedor_id, created_by, prepared):
    subtotal = sum(i["precio_estandar"] * i["cantidad"] for i in prepared)
    total = sum(i["precio_unitario"] * i["cantidad"] for i in prepared)
    descuento = subtotal - total
    cursor.execute(
        """
        INSERT INTO ordenes_venta
            (cliente_id, sucursal_id, ubicacion_stock_id, vendedor_id, creado_por_usuario_id, codigo_orden,
             estado, subtotal, descuento_total, total_estimado, created_at, updated_at)
        VALUES (%s,%s,%s,%s,%s,CONCAT('ORD-', DATE_FORMAT(NOW(), '%Y%m%d'), '-', UUID_SHORT()),'APROBADA',%s,%s,%s,NOW(),NOW())
        """,
        (cliente_id, sucursal_id, ubicacion_stock_id, vendedor_id, created_by, subtotal, descuento, total),
    )
    order_id = cursor.lastrowid
    insert_order_details(cursor, cliente_id, order_id, prepared)
    return order_id


def items_from_order(cursor, order_id):
    cursor.execute(
        """
        SELECT od.producto_id, od.producto_presentacion_id AS presentacion_id, od.cantidad,
               od.precio_estandar, od.precio_minimo_venta AS precio_minimo, od.precio_unitario,
               pp.factor_unidad_base, pp.nombre AS presentacion_nombre, p.nombre AS producto_nombre
        FROM orden_venta_detalles od
        JOIN producto_presentaciones pp ON pp.id = od.producto_presentacion_id
        JOIN productos p ON p.id = od.producto_id
        WHERE od.orden_venta_id = %s
        """,
        (order_id,),
    )
    rows = cursor.fetchall()
    for row in rows:
        row["cantidad"] = whole_units(row["cantidad"])
        row["cantidad_base"] = row["cantidad"] * whole_units(row["factor_unidad_base"], "factor de presentación")
        row["precio_estandar"] = money(row["precio_estandar"])
        row["precio_minimo"] = money(row["precio_minimo"])
        row["precio_unitario"] = money(row["precio_unitario"])
    return rows


def discount_stock(cursor, cliente_id, ubicacion_stock_id, item):
    cursor.execute("SELECT id, cantidad_disponible FROM inventarios WHERE cliente_id=%s AND ubicacion_stock_id=%s AND producto_id=%s FOR UPDATE", (cliente_id, ubicacion_stock_id, item["producto_id"]))
    inv = cursor.fetchone()
    if not inv:
        raise ValueError(f"No existe stock configurado para {item['producto_nombre']} en esta ubicación.")
    try:
        stock_decimal = Decimal(str(inv.get("cantidad_disponible") or "0"))
        requested_decimal = Decimal(str(item.get("cantidad_base") or "0"))
    except Exception as exc:
        raise ValueError(f"Stock inválido para {item['producto_nombre']}.") from exc
    if requested_decimal <= 0:
        raise ValueError(f"Cantidad inválida para {item['producto_nombre']}.")
    stock_disponible = int(stock_decimal) if stock_decimal == stock_decimal.to_integral_value() else int(stock_decimal.to_integral_value(rounding='ROUND_FLOOR'))
    cantidad_solicitada = int(requested_decimal) if requested_decimal == requested_decimal.to_integral_value() else int(requested_decimal.to_integral_value(rounding='ROUND_CEILING'))
    if stock_disponible < cantidad_solicitada:
        raise ValueError(f"Stock insuficiente para {item['producto_nombre']}. Disponible: {stock_disponible}. Solicitado: {cantidad_solicitada}.")
    cursor.execute("UPDATE inventarios SET cantidad_disponible = cantidad_disponible - %s, updated_at = NOW() WHERE id = %s", (cantidad_solicitada, inv["id"]))


def get_sale_receipt(cliente_id: int, venta_id: int):
    with db_cursor() as cursor:
        cursor.execute(
            """
            SELECT v.*, c.nombre_comercial AS cliente_nombre, s.nombre AS sucursal_nombre,
                   caj.username AS cajero_username, ven.username AS vendedor_username, vp.metodo_pago
            FROM ventas v
            JOIN clientes c ON c.id = v.cliente_id
            LEFT JOIN sucursales s ON s.id = v.sucursal_id
            LEFT JOIN usuarios caj ON caj.id = v.cajero_id
            LEFT JOIN usuarios ven ON ven.id = v.vendedor_id
            LEFT JOIN venta_pagos vp ON vp.venta_id = v.id
            WHERE v.cliente_id = %s AND v.id = %s
            LIMIT 1
            """,
            (cliente_id, venta_id),
        )
        sale = cursor.fetchone()
        if not sale:
            return None
        cursor.execute(
            """
            SELECT vd.*, p.nombre, p.codigo_producto, pp.nombre AS presentacion
            FROM venta_detalles vd
            JOIN productos p ON p.id = vd.producto_id
            JOIN producto_presentaciones pp ON pp.id = vd.producto_presentacion_id
            WHERE vd.venta_id = %s
            ORDER BY vd.id
            """,
            (venta_id,),
        )
        sale["detalles"] = cursor.fetchall()
        return sale
