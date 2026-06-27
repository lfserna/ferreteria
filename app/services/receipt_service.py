from app.database import db_cursor
from app.services.sales_service import get_sale_receipt as base_get_sale_receipt


def _ventas_extra_data(cliente_id, venta_id):
    with db_cursor() as cursor:
        cursor.execute("SHOW COLUMNS FROM ventas")
        cols = {row["Field"] for row in cursor.fetchall()}
        select_parts = []
        for field in ["cliente_nombre", "cliente_celular", "cliente_carnet", "tipo_entrega", "ciudad_destino", "detalle_envio", "cliente_final_id"]:
            select_parts.append(field if field in cols else f"NULL AS {field}")
        cursor.execute(f"SELECT {', '.join(select_parts)} FROM ventas WHERE cliente_id=%s AND id=%s LIMIT 1", (cliente_id, venta_id))
        return cursor.fetchone() or {}


def get_sale_receipt(cliente_id: int, venta_id: int):
    sale = base_get_sale_receipt(cliente_id, venta_id)
    if not sale:
        return None
    sale["negocio_nombre"] = sale.get("cliente_nombre")
    extra = _ventas_extra_data(cliente_id, venta_id)
    sale["cliente_final_nombre"] = extra.get("cliente_nombre") or sale.get("cliente_final_nombre") or None
    sale["cliente_celular"] = extra.get("cliente_celular") or sale.get("cliente_celular")
    sale["cliente_carnet"] = extra.get("cliente_carnet") or sale.get("cliente_carnet")
    sale["tipo_entrega"] = extra.get("tipo_entrega") or sale.get("tipo_entrega")
    sale["ciudad_destino"] = extra.get("ciudad_destino") or sale.get("ciudad_destino")
    sale["detalle_envio"] = extra.get("detalle_envio") or sale.get("detalle_envio")
    sale["cliente_final_id"] = extra.get("cliente_final_id") or sale.get("cliente_final_id")
    if sale.get("cliente_final_id"):
        with db_cursor() as cursor:
            cursor.execute(
                """
                SELECT nombre, apellido_paterno, apellido_materno, nombre_completo, celular, nit_ci, carnet, ciudad, detalle_envio
                FROM clientes_finales
                WHERE cliente_id=%s AND id=%s
                LIMIT 1
                """,
                (cliente_id, sale["cliente_final_id"]),
            )
            customer = cursor.fetchone()
        if customer:
            full_name = customer.get("nombre_completo") or " ".join([part for part in [customer.get("nombre"), customer.get("apellido_paterno"), customer.get("apellido_materno")] if part])
            sale["cliente_final_nombre"] = full_name or sale.get("cliente_final_nombre")
            sale["cliente_celular"] = sale.get("cliente_celular") or customer.get("celular")
            sale["cliente_carnet"] = sale.get("cliente_carnet") or customer.get("carnet") or customer.get("nit_ci")
            sale["ciudad_destino"] = sale.get("ciudad_destino") or customer.get("ciudad")
            sale["detalle_envio"] = sale.get("detalle_envio") or customer.get("detalle_envio")
    return sale
