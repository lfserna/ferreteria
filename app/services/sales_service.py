import re
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from app.database import db_cursor, db_transaction
from app.services.audit_service import log_audit
from app.services.price_schema_service import ensure_extra_price_columns

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


def truthy(value):
    return str(value).lower() in {"1", "true", "si", "sí", "on", "yes"}


def column_info(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    return {row["Field"]: row for row in cursor.fetchall()}


def table_columns(cursor, table_name):
    return set(column_info(cursor, table_name).keys())


def index_exists(cursor, table_name, index_name):
    cursor.execute("""
        SELECT COUNT(*) AS total FROM INFORMATION_SCHEMA.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s AND INDEX_NAME = %s
    """, (table_name, index_name))
    return int((cursor.fetchone() or {}).get("total") or 0) > 0


def add_column(cursor, table_name, columns, name, definition):
    if name not in columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}")
        columns.add(name)


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


def ensure_customer_schema(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS clientes_finales (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            cliente_id BIGINT UNSIGNED NOT NULL,
            nombre VARCHAR(120) NOT NULL,
            apellido_paterno VARCHAR(120) NULL,
            apellido_materno VARCHAR(120) NULL,
            nit_ci VARCHAR(40) NULL,
            celular VARCHAR(40) NULL,
            email VARCHAR(120) NULL,
            direccion VARCHAR(255) NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NULL,
            deleted_at DATETIME NULL,
            KEY idx_cliente_final_cliente (cliente_id)
        ) ENGINE=InnoDB
    """)
    cols = table_columns(cursor, "clientes_finales")
    add_column(cursor, "clientes_finales", cols, "nombre_completo", "VARCHAR(180) NULL")
    add_column(cursor, "clientes_finales", cols, "carnet", "VARCHAR(60) NULL")
    add_column(cursor, "clientes_finales", cols, "ciudad", "VARCHAR(80) NULL")
    add_column(cursor, "clientes_finales", cols, "detalle_envio", "VARCHAR(255) NULL")
    if not index_exists(cursor, "clientes_finales", "idx_clientes_finales_carnet"):
        cursor.execute("ALTER TABLE clientes_finales ADD INDEX idx_clientes_finales_carnet (cliente_id, carnet)")


def ensure_sales_schema():
    with db_cursor(commit=True) as cursor:
        ensure_customer_schema(cursor)
        ensure_extra_price_columns(cursor)
        columns = table_columns(cursor, "ventas")
        add_column(cursor, "ventas", columns, "idempotency_key", "VARCHAR(120) NULL")
        add_column(cursor, "ventas", columns, "numero_comprobante", "BIGINT UNSIGNED NULL")
        add_column(cursor, "ventas", columns, "caja_sesion_id", "BIGINT UNSIGNED NULL")
        add_column(cursor, "ventas", columns, "cliente_nombre", "VARCHAR(180) NULL")
        add_column(cursor, "ventas", columns, "cliente_celular", "VARCHAR(40) NULL")
        add_column(cursor, "ventas", columns, "cliente_carnet", "VARCHAR(60) NULL")
        add_column(cursor, "ventas", columns, "tipo_entrega", "VARCHAR(20) NULL")
        add_column(cursor, "ventas", columns, "ciudad_destino", "VARCHAR(80) NULL")
        add_column(cursor, "ventas", columns, "detalle_envio", "VARCHAR(255) NULL")
        cursor.execute("UPDATE ventas SET numero_comprobante = id WHERE numero_comprobante IS NULL")
        detalle_cols = table_columns(cursor, "venta_detalles")
        add_column(cursor, "venta_detalles", detalle_cols, "es_yapa", "TINYINT(1) NOT NULL DEFAULT 0")
        orden_cols = table_columns(cursor, "orden_venta_detalles")
        add_column(cursor, "orden_venta_detalles", orden_cols, "es_yapa", "TINYINT(1) NOT NULL DEFAULT 0")
        if not index_exists(cursor, "ventas", "idx_ventas_idempotency"):
            cursor.execute("ALTER TABLE ventas ADD INDEX idx_ventas_idempotency (cliente_id, idempotency_key)")
        if not index_exists(cursor, "ventas", "idx_ventas_numero_comprobante"):
            cursor.execute("ALTER TABLE ventas ADD INDEX idx_ventas_numero_comprobante (cliente_id, numero_comprobante)")
        if not index_exists(cursor, "ventas", "idx_ventas_caja_sesion"):
            cursor.execute("ALTER TABLE ventas ADD INDEX idx_ventas_caja_sesion (cliente_id, caja_sesion_id)")
    return True


def assign_receipt_number(cursor, venta_id):
    columns = table_columns(cursor, "ventas")
    if "numero_comprobante" not in columns:
        return venta_id
    cursor.execute("UPDATE ventas SET numero_comprobante=%s WHERE id=%s AND numero_comprobante IS NULL", (venta_id, venta_id))
    return venta_id


def user_display_name(row):
    if not row:
        return "-"
    name = " ".join([part for part in [row.get("nombres"), row.get("apellido_paterno")] if part])
    return name or row.get("username") or "-"


def list_available_sellers(cliente_id: int, sucursal_id: int | None = None):
    with db_cursor() as cursor:
        params = [cliente_id]
        sucursal_filter = ""
        if sucursal_id:
            sucursal_filter = "AND (u.sucursal_id = %s OR ur.sucursal_id = %s OR u.sucursal_id IS NULL OR ur.sucursal_id IS NULL)"
            params.extend([sucursal_id, sucursal_id])
        cursor.execute(f"""
            SELECT DISTINCT u.id, u.username, u.nombres, u.apellido_paterno, u.sucursal_id
            FROM usuarios u
            JOIN usuario_roles ur ON ur.usuario_id = u.id AND ur.cliente_id = u.cliente_id AND ur.estado = 'ACTIVO'
            JOIN roles r ON r.id = ur.rol_id AND r.estado = 'ACTIVO'
            WHERE u.cliente_id = %s AND u.estado = 'ACTIVO' AND r.codigo = 'VENDEDOR' {sucursal_filter}
            ORDER BY u.nombres, u.apellido_paterno, u.username
        """, tuple(params))
        sellers = cursor.fetchall()
        for seller in sellers:
            seller["nombre_visible"] = user_display_name(seller)
        return sellers


def validate_seller_id(cursor, cliente_id: int, seller_id):
    if seller_id in (None, "", 0, "0"):
        return None
    try:
        seller_id = int(seller_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Vendedor inválido.") from exc
    cursor.execute("SELECT id FROM usuarios WHERE cliente_id=%s AND id=%s AND estado='ACTIVO' LIMIT 1", (cliente_id, seller_id))
    if not cursor.fetchone():
        raise ValueError("El vendedor seleccionado no existe o no está activo.")
    return seller_id


def fetch_order_details(cursor, order_id):
    cols = table_columns(cursor, "orden_venta_detalles")
    yapa_expr = "COALESCE(od.es_yapa,0) AS es_yapa" if "es_yapa" in cols else "0 AS es_yapa"
    cursor.execute(f"""
        SELECT od.*, {yapa_expr}, p.nombre, p.codigo_producto, pp.nombre AS presentacion
        FROM orden_venta_detalles od
        JOIN productos p ON p.id = od.producto_id
        JOIN producto_presentaciones pp ON pp.id = od.producto_presentacion_id
        WHERE od.orden_venta_id = %s
        ORDER BY od.id
    """, (order_id,))
    return cursor.fetchall()


def list_pending_orders(cliente_id: int, sucursal_id: int | None):
    with db_cursor() as cursor:
        params = [cliente_id]
        where_sucursal = ""
        if sucursal_id:
            where_sucursal = "AND ov.sucursal_id = %s"
            params.append(sucursal_id)
        cursor.execute(f"""
            SELECT ov.id, ov.codigo_orden, ov.subtotal, ov.descuento_total, ov.total_estimado,
                   ov.created_at, ov.vendedor_id,
                   COALESCE(CONCAT_WS(' ', u.nombres, u.apellido_paterno), u.username) AS vendedor_nombre,
                   u.username AS vendedor_username, COUNT(od.id) AS items
            FROM ordenes_venta ov
            LEFT JOIN usuarios u ON u.id = ov.vendedor_id
            LEFT JOIN orden_venta_detalles od ON od.orden_venta_id = ov.id
            WHERE ov.cliente_id = %s AND ov.estado = 'ENVIADA_CAJA' {where_sucursal}
            GROUP BY ov.id, u.nombres, u.apellido_paterno, u.username
            ORDER BY ov.created_at ASC LIMIT 25
        """, tuple(params))
        orders = cursor.fetchall()
        for order in orders:
            order["detalles"] = fetch_order_details(cursor, order["id"])
        return orders


def get_order(cliente_id: int, orden_id: int):
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT ov.*, COALESCE(CONCAT_WS(' ', u.nombres, u.apellido_paterno), u.username) AS vendedor_nombre,
                   u.username AS vendedor_username
            FROM ordenes_venta ov LEFT JOIN usuarios u ON u.id = ov.vendedor_id
            WHERE ov.cliente_id=%s AND ov.id=%s LIMIT 1
        """, (cliente_id, orden_id))
        order = cursor.fetchone()
        if not order:
            return None
        order["detalles"] = fetch_order_details(cursor, orden_id)
        return order


def prepare_items(cursor, cliente_id, items):
    prepared = []
    for raw in items:
        producto_id = int(raw["producto_id"])
        presentacion_id = int(raw["presentacion_id"])
        cantidad = whole_units(raw.get("cantidad", 1))
        es_yapa = truthy(raw.get("es_yapa"))
        precio_unitario = money(raw.get("precio_unitario"))
        cursor.execute("""
            SELECT p.id AS producto_id, p.nombre AS producto_nombre, pp.id AS presentacion_id,
                   pp.nombre AS presentacion_nombre, COALESCE(pp.factor_unidad_base, 1) AS factor_unidad_base,
                   pr.precio_venta_estandar, pr.precio_minimo_venta
            FROM productos p
            JOIN producto_presentaciones pp ON pp.producto_id = p.id AND pp.id = %s
            LEFT JOIN producto_precios pr ON pr.id=(SELECT pr2.id FROM producto_precios pr2 WHERE pr2.producto_presentacion_id=pp.id ORDER BY CASE WHEN pr2.estado='ACTIVO' THEN 0 ELSE 1 END, pr2.id DESC LIMIT 1)
            WHERE p.cliente_id = %s AND p.id = %s AND p.estado = 'ACTIVO'
            LIMIT 1
        """, (presentacion_id, cliente_id, producto_id))
        product = cursor.fetchone()
        if not product or product.get("precio_venta_estandar") is None or product.get("precio_minimo_venta") is None:
            raise ValueError("Producto inválido o sin precio de venta configurado.")
        factor = whole_units(product["factor_unidad_base"], "factor de presentación")
        precio_estandar = money(product["precio_venta_estandar"])
        precio_minimo = money(product["precio_minimo_venta"])
        if es_yapa:
            precio_unitario = Decimal("0.00")
            precio_minimo_detalle = Decimal("0.00")
        else:
            precio_minimo_detalle = precio_minimo
            if precio_unitario <= 0:
                precio_unitario = precio_estandar
            if precio_unitario < precio_minimo:
                raise ValueError(f"{product['producto_nombre']} no puede venderse por debajo de {precio_minimo}.")
            if precio_unitario > precio_estandar:
                raise ValueError(f"{product['producto_nombre']} no puede venderse por encima del precio estándar.")
        prepared.append({"producto_id": product["producto_id"], "producto_nombre": product["producto_nombre"], "presentacion_id": product["presentacion_id"], "presentacion_nombre": product["presentacion_nombre"], "cantidad": cantidad, "cantidad_base": cantidad * factor, "precio_estandar": precio_estandar, "precio_minimo": precio_minimo_detalle, "precio_unitario": precio_unitario, "es_yapa": 1 if es_yapa else 0})
    return prepared


def create_order_from_cart(*, cliente_id, sucursal_id, ubicacion_stock_id, vendedor_id, created_by, items):
    if not items:
        raise ValueError("El carrito está vacío.")
    ensure_sales_schema()
    with db_transaction() as (cursor, _connection):
        vendedor_id = validate_seller_id(cursor, cliente_id, vendedor_id) or created_by
        prepared = prepare_items(cursor, cliente_id, items)
        subtotal = sum(i["precio_estandar"] * i["cantidad"] for i in prepared)
        total = sum(i["precio_unitario"] * i["cantidad"] for i in prepared)
        descuento = subtotal - total
        cursor.execute("""
            INSERT INTO ordenes_venta
                (cliente_id, sucursal_id, ubicacion_stock_id, vendedor_id, creado_por_usuario_id,
                 codigo_orden, estado, subtotal, descuento_total, total_estimado, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,CONCAT('ORD-', DATE_FORMAT(NOW(), '%Y%m%d'), '-', UUID_SHORT()), 'ENVIADA_CAJA',%s,%s,%s,NOW(),NOW())
        """, (cliente_id, sucursal_id, ubicacion_stock_id, vendedor_id, created_by, subtotal, descuento, total))
        order_id = cursor.lastrowid
        insert_order_details(cursor, cliente_id, order_id, prepared)
        log_audit(cursor, cliente_id=cliente_id, usuario_id=created_by, modulo="VENTAS", accion="ENVIAR_ORDEN_CAJA", tabla_afectada="ordenes_venta", registro_id=order_id, valor_nuevo={"total": total})
        return {"order_id": order_id, "total": str(total)}


def insert_payment(cursor, cliente_id, venta_id, metodo_pago, total):
    columns = table_columns(cursor, "venta_pagos")
    estado_value = enum_value_for_column(cursor, "venta_pagos", "estado", ["CONFIRMADO", "PAGADO", "PAGADA", "COMPLETADO", "ACTIVO"])
    if "estado" in columns and estado_value:
        cursor.execute("INSERT INTO venta_pagos (cliente_id, venta_id, metodo_pago, monto, estado, created_at) VALUES (%s,%s,%s,%s,%s,NOW())", (cliente_id, venta_id, metodo_pago, total, estado_value))
    else:
        cursor.execute("INSERT INTO venta_pagos (cliente_id, venta_id, metodo_pago, monto, created_at) VALUES (%s,%s,%s,%s,NOW())", (cliente_id, venta_id, metodo_pago, total))


def clean_customer_data(cliente_data):
    data = cliente_data or {}
    tipo = "ENVIO" if str(data.get("tipo_entrega") or "TIENDA").upper() == "ENVIO" else "TIENDA"
    result = {
        "nombre": (data.get("nombre") or "").strip(),
        "celular": (data.get("celular") or "").strip(),
        "carnet": (data.get("carnet") or "").strip(),
        "tipo_entrega": tipo,
        "ciudad_destino": (data.get("ciudad_destino") or "").strip() if tipo == "ENVIO" else "",
        "detalle_envio": (data.get("detalle_envio") or "").strip() if tipo == "ENVIO" else "",
    }
    return result


def upsert_customer(cursor, cliente_id, cliente_data):
    ensure_customer_schema(cursor)
    data = clean_customer_data(cliente_data)
    if not any([data["nombre"], data["celular"], data["carnet"], data["ciudad_destino"], data["detalle_envio"]]):
        return None, data
    cols = table_columns(cursor, "clientes_finales")
    customer_id = None
    if data["carnet"]:
        filters = []
        params = [cliente_id]
        if "carnet" in cols:
            filters.append("carnet=%s"); params.append(data["carnet"])
        if "nit_ci" in cols:
            filters.append("nit_ci=%s"); params.append(data["carnet"])
        if filters:
            cursor.execute(f"SELECT id FROM clientes_finales WHERE cliente_id=%s AND ({' OR '.join(filters)}) LIMIT 1", tuple(params))
            row = cursor.fetchone()
            customer_id = row["id"] if row else None
    values = {}
    if "nombre" in cols:
        values["nombre"] = data["nombre"] or "Cliente"
    if "nombre_completo" in cols:
        values["nombre_completo"] = data["nombre"] or None
    if "celular" in cols:
        values["celular"] = data["celular"] or None
    if "carnet" in cols:
        values["carnet"] = data["carnet"] or None
    if "nit_ci" in cols:
        values["nit_ci"] = data["carnet"] or None
    if "ciudad" in cols:
        values["ciudad"] = data["ciudad_destino"] or None
    if "detalle_envio" in cols:
        values["detalle_envio"] = data["detalle_envio"] or None
    if "direccion" in cols and data["detalle_envio"]:
        values["direccion"] = data["detalle_envio"]
    if customer_id:
        set_parts = [f"{key}=%s" for key in values]
        if "updated_at" in cols:
            set_parts.append("updated_at=NOW()")
        cursor.execute(f"UPDATE clientes_finales SET {', '.join(set_parts)} WHERE id=%s", tuple(values.values()) + (customer_id,))
        return customer_id, data
    insert_cols = ["cliente_id"] + list(values.keys())
    placeholders = ["%s"] * len(insert_cols)
    cursor.execute(f"INSERT INTO clientes_finales ({', '.join(insert_cols)}) VALUES ({', '.join(placeholders)})", tuple([cliente_id] + list(values.values())))
    return cursor.lastrowid, data


def insert_sale(cursor, venta_cols, values):
    cols, placeholders, params = [], [], []
    def add(name, value=None, expr=None):
        if name in venta_cols:
            cols.append(name); placeholders.append(expr or "%s")
            if expr is None:
                params.append(value)
    for key in ["cliente_id", "sucursal_id", "ubicacion_stock_id", "orden_venta_id", "cajero_id", "vendedor_id"]:
        add(key, values.get(key))
    add("caja_sesion_id", values.get("caja_sesion_id"))
    add("cliente_final_id", values.get("cliente_final_id"))
    add("numero_venta", expr="CONCAT('V-', DATE_FORMAT(NOW(), '%Y%m%d'), '-', UUID_SHORT())")
    add("fecha_venta", expr="NOW()")
    for key in ["subtotal", "descuento_total", "total", "estado", "idempotency_key", "cliente_nombre", "cliente_celular", "cliente_carnet", "tipo_entrega", "ciudad_destino", "detalle_envio"]:
        add(key, values.get(key))
    add("created_at", expr="NOW()"); add("updated_at", expr="NOW()")
    cursor.execute(f"INSERT INTO ventas ({', '.join(cols)}) VALUES ({', '.join(placeholders)})", tuple(params))
    return cursor.lastrowid


def confirm_sale_from_cart(*, cliente_id, sucursal_id, ubicacion_stock_id, cajero_id, vendedor_id, created_by, items, metodo_pago, idempotency_key, orden_id=None, caja_sesion_id=None, cliente_data=None):
    if metodo_pago not in PAYMENT_METHODS:
        raise ValueError("Método de pago inválido.")
    if not idempotency_key:
        raise ValueError("Falta la llave de idempotencia.")
    ensure_sales_schema()
    with db_transaction() as (cursor, _connection):
        selected_seller_id = validate_seller_id(cursor, cliente_id, vendedor_id)
        cursor.execute("SELECT id, numero_venta, numero_comprobante, total FROM ventas WHERE cliente_id=%s AND idempotency_key=%s LIMIT 1", (cliente_id, idempotency_key))
        existing = cursor.fetchone()
        if existing:
            return {"venta_id": existing["id"], "numero_venta": existing["numero_venta"], "numero_comprobante": existing.get("numero_comprobante"), "total": str(existing["total"]), "duplicada": True}
        if orden_id:
            cursor.execute("SELECT * FROM ordenes_venta WHERE cliente_id=%s AND id=%s AND estado IN ('ENVIADA_CAJA','EN_REVISION_CAJA','APROBADA') FOR UPDATE", (cliente_id, orden_id))
            order = cursor.fetchone()
            if not order:
                raise ValueError("La orden no existe o ya fue procesada.")
            selected_seller_id = order["vendedor_id"]
            sucursal_id = order["sucursal_id"]
            ubicacion_stock_id = order["ubicacion_stock_id"]
            prepared = items_from_order(cursor, orden_id)
        else:
            prepared = prepare_items(cursor, cliente_id, items)
            orden_id = create_internal_order(cursor, cliente_id, sucursal_id, ubicacion_stock_id, selected_seller_id, created_by, prepared)
        subtotal = sum(i["precio_estandar"] * i["cantidad"] for i in prepared)
        total = sum(i["precio_unitario"] * i["cantidad"] for i in prepared)
        descuento = subtotal - total
        for item in prepared:
            discount_stock(cursor, cliente_id, ubicacion_stock_id, item)
        cliente_final_id, customer = upsert_customer(cursor, cliente_id, cliente_data)
        venta_cols = table_columns(cursor, "ventas")
        venta_id = insert_sale(cursor, venta_cols, {"cliente_id": cliente_id, "sucursal_id": sucursal_id, "ubicacion_stock_id": ubicacion_stock_id, "orden_venta_id": orden_id, "cajero_id": cajero_id, "vendedor_id": selected_seller_id, "caja_sesion_id": caja_sesion_id, "cliente_final_id": cliente_final_id, "subtotal": subtotal, "descuento_total": descuento, "total": total, "estado": "PAGADA", "idempotency_key": idempotency_key, "cliente_nombre": customer["nombre"] or None, "cliente_celular": customer["celular"] or None, "cliente_carnet": customer["carnet"] or None, "tipo_entrega": customer["tipo_entrega"], "ciudad_destino": customer["ciudad_destino"] or None, "detalle_envio": customer["detalle_envio"] or None})
        numero_comprobante = assign_receipt_number(cursor, venta_id)
        insert_sale_details(cursor, cliente_id, venta_id, prepared)
        insert_payment(cursor, cliente_id, venta_id, metodo_pago, total)
        cursor.execute("UPDATE ordenes_venta SET estado='FACTURADA', cajero_id=%s, updated_at=NOW() WHERE id=%s", (cajero_id, orden_id))
        log_audit(cursor, cliente_id=cliente_id, usuario_id=created_by, modulo="VENTAS", accion="CONFIRMAR_VENTA", tabla_afectada="ventas", registro_id=venta_id, valor_nuevo={"total": total, "metodo_pago": metodo_pago, "numero_comprobante": numero_comprobante, "vendedor_id": selected_seller_id, "caja_sesion_id": caja_sesion_id, "cliente": customer})
        cursor.execute("SELECT numero_venta, numero_comprobante FROM ventas WHERE id=%s", (venta_id,))
        row = cursor.fetchone()
        return {"venta_id": venta_id, "numero_venta": row["numero_venta"], "numero_comprobante": row.get("numero_comprobante"), "total": str(total), "duplicada": False}


def insert_detail_row(cursor, table_name, base_cols, values):
    cols = list(base_cols)
    params = [values[col] for col in cols]
    placeholders = ["%s"] * len(cols)
    available = table_columns(cursor, table_name)
    if "es_yapa" in available:
        cols.append("es_yapa"); placeholders.append("%s"); params.append(values.get("es_yapa", 0))
    if "updated_at" in available and table_name == "orden_venta_detalles":
        cols.append("updated_at"); placeholders.append("NOW()")
    cursor.execute(f"INSERT INTO {table_name} ({', '.join(cols)}) VALUES ({', '.join(placeholders)})", tuple(params))


def insert_order_details(cursor, cliente_id, order_id, prepared):
    for item in prepared:
        item_discount = (item["precio_estandar"] - item["precio_unitario"]) * item["cantidad"]
        insert_detail_row(cursor, "orden_venta_detalles", ["cliente_id", "orden_venta_id", "producto_id", "producto_presentacion_id", "cantidad", "precio_estandar", "precio_minimo_venta", "precio_unitario", "descuento", "subtotal", "created_at"], {"cliente_id": cliente_id, "orden_venta_id": order_id, "producto_id": item["producto_id"], "producto_presentacion_id": item["presentacion_id"], "cantidad": item["cantidad"], "precio_estandar": item["precio_estandar"], "precio_minimo_venta": item["precio_minimo"], "precio_unitario": item["precio_unitario"], "descuento": item_discount, "subtotal": item["precio_unitario"] * item["cantidad"], "created_at": None, "es_yapa": item.get("es_yapa", 0)})


def insert_sale_details(cursor, cliente_id, venta_id, prepared):
    for item in prepared:
        item_discount = (item["precio_estandar"] - item["precio_unitario"]) * item["cantidad"]
        item_subtotal = item["precio_unitario"] * item["cantidad"]
        insert_detail_row(cursor, "venta_detalles", ["cliente_id", "venta_id", "producto_id", "producto_presentacion_id", "cantidad", "precio_estandar", "precio_minimo_venta", "precio_unitario", "descuento", "subtotal", "created_at"], {"cliente_id": cliente_id, "venta_id": venta_id, "producto_id": item["producto_id"], "producto_presentacion_id": item["presentacion_id"], "cantidad": item["cantidad"], "precio_estandar": item["precio_estandar"], "precio_minimo_venta": item["precio_minimo"], "precio_unitario": item["precio_unitario"], "descuento": item_discount, "subtotal": item_subtotal, "created_at": None, "es_yapa": item.get("es_yapa", 0)})
        cursor.execute("INSERT INTO inventario_movimientos (cliente_id, producto_id, ubicacion_origen_id, tipo_movimiento, cantidad, referencia_tipo, referencia_id, usuario_id, observacion, created_at) VALUES (%s,%s,%s,'VENTA',%s,'VENTA',%s,%s,%s,NOW())", (cliente_id, item["producto_id"], None, item["cantidad_base"], venta_id, None, f"Venta de {item['cantidad']} {item['presentacion_nombre']}{' (YAPA)' if item.get('es_yapa') else ''}"))


def create_internal_order(cursor, cliente_id, sucursal_id, ubicacion_stock_id, vendedor_id, created_by, prepared):
    subtotal = sum(i["precio_estandar"] * i["cantidad"] for i in prepared)
    total = sum(i["precio_unitario"] * i["cantidad"] for i in prepared)
    descuento = subtotal - total
    cursor.execute("""
        INSERT INTO ordenes_venta
            (cliente_id, sucursal_id, ubicacion_stock_id, vendedor_id, creado_por_usuario_id, codigo_orden,
             estado, subtotal, descuento_total, total_estimado, created_at, updated_at)
        VALUES (%s,%s,%s,%s,%s,CONCAT('ORD-', DATE_FORMAT(NOW(), '%Y%m%d'), '-', UUID_SHORT()),'APROBADA',%s,%s,%s,NOW(),NOW())
    """, (cliente_id, sucursal_id, ubicacion_stock_id, vendedor_id, created_by, subtotal, descuento, total))
    order_id = cursor.lastrowid
    insert_order_details(cursor, cliente_id, order_id, prepared)
    return order_id


def items_from_order(cursor, order_id):
    cols = table_columns(cursor, "orden_venta_detalles")
    yapa_expr = "COALESCE(od.es_yapa,0) AS es_yapa" if "es_yapa" in cols else "0 AS es_yapa"
    cursor.execute(f"""
        SELECT od.producto_id, od.producto_presentacion_id AS presentacion_id, od.cantidad,
               od.precio_estandar, od.precio_minimo_venta AS precio_minimo, od.precio_unitario, {yapa_expr},
               pp.factor_unidad_base, pp.nombre AS presentacion_nombre, p.nombre AS producto_nombre
        FROM orden_venta_detalles od
        JOIN producto_presentaciones pp ON pp.id = od.producto_presentacion_id
        JOIN productos p ON p.id = od.producto_id
        WHERE od.orden_venta_id = %s
    """, (order_id,))
    rows = cursor.fetchall()
    for row in rows:
        row["cantidad"] = whole_units(row["cantidad"])
        row["cantidad_base"] = row["cantidad"] * whole_units(row["factor_unidad_base"], "factor de presentación")
        row["precio_estandar"] = money(row["precio_estandar"])
        row["precio_minimo"] = money(row["precio_minimo"])
        row["precio_unitario"] = money(row["precio_unitario"])
        row["es_yapa"] = 1 if truthy(row.get("es_yapa")) else 0
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
    stock_disponible = int(stock_decimal) if stock_decimal == stock_decimal.to_integral_value() else int(stock_decimal.to_integral_value(rounding=ROUND_FLOOR))
    cantidad_solicitada = int(requested_decimal) if requested_decimal == requested_decimal.to_integral_value() else int(requested_decimal.to_integral_value(rounding=ROUND_CEILING))
    if stock_disponible < cantidad_solicitada:
        raise ValueError(f"Stock insuficiente para {item['producto_nombre']}. Disponible: {stock_disponible}. Solicitado: {cantidad_solicitada}.")
    cursor.execute("UPDATE inventarios SET cantidad_disponible = cantidad_disponible - %s, updated_at = NOW() WHERE id = %s", (cantidad_solicitada, inv["id"]))


def get_sale_receipt(cliente_id: int, venta_id: int):
    ensure_sales_schema()
    with db_cursor() as cursor:
        cursor.execute("""
            SELECT v.*, c.nombre_comercial AS cliente_nombre, s.nombre AS sucursal_nombre,
                   caj.username AS cajero_username, COALESCE(CONCAT_WS(' ', caj.nombres, caj.apellido_paterno), caj.username) AS cajero_nombre,
                   ven.username AS vendedor_username, COALESCE(CONCAT_WS(' ', ven.nombres, ven.apellido_paterno), ven.username) AS vendedor_nombre,
                   vp.metodo_pago
            FROM ventas v
            JOIN clientes c ON c.id = v.cliente_id
            LEFT JOIN sucursales s ON s.id = v.sucursal_id
            LEFT JOIN usuarios caj ON caj.id = v.cajero_id
            LEFT JOIN usuarios ven ON ven.id = v.vendedor_id
            LEFT JOIN venta_pagos vp ON vp.venta_id = v.id
            WHERE v.cliente_id = %s AND v.id = %s
            LIMIT 1
        """, (cliente_id, venta_id))
        sale = cursor.fetchone()
        if not sale:
            return None
        if not sale.get("numero_comprobante"):
            sale["numero_comprobante"] = assign_receipt_number(cursor, sale["id"])
        cols = table_columns(cursor, "venta_detalles")
        yapa_expr = "COALESCE(vd.es_yapa,0) AS es_yapa" if "es_yapa" in cols else "0 AS es_yapa"
        cursor.execute(f"""
            SELECT vd.*, {yapa_expr}, p.nombre, p.codigo_producto, pp.nombre AS presentacion
            FROM venta_detalles vd
            JOIN productos p ON p.id = vd.producto_id
            JOIN producto_presentaciones pp ON pp.id = vd.producto_presentacion_id
            WHERE vd.venta_id = %s
            ORDER BY vd.id
        """, (venta_id,))
        sale["detalles"] = cursor.fetchall()
        return sale
