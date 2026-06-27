from flask import Blueprint, g, jsonify, request

from app.database import db_cursor
from app.utils.security import login_required
from app.utils.serializers import to_jsonable

sales_customer_bp = Blueprint("sales_customer", __name__, url_prefix="/ventas")


def table_exists(cursor, table_name):
    cursor.execute("SELECT COUNT(*) AS total FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s", (table_name,))
    return int((cursor.fetchone() or {}).get("total") or 0) > 0


def table_columns(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    return {row["Field"] for row in cursor.fetchall()}


def name_expr(cols):
    if "nombre_completo" in cols:
        return "COALESCE(nombre_completo, CONCAT_WS(' ', nombre, apellido_paterno, apellido_materno))"
    return "CONCAT_WS(' ', nombre, apellido_paterno, apellido_materno)"


def doc_expr(cols):
    parts = [field for field in ["carnet", "nit_ci"] if field in cols]
    if not parts:
        return "NULL"
    return f"COALESCE({', '.join(parts)})" if len(parts) > 1 else parts[0]


def total_compras(cursor, cliente_id, cliente_final_id, carnet):
    if not table_exists(cursor, "ventas"):
        return "0.00"
    vcols = table_columns(cursor, "ventas")
    customer_filters = []
    params = [cliente_id]
    if cliente_final_id and "cliente_final_id" in vcols:
        customer_filters.append("cliente_final_id=%s")
        params.append(cliente_final_id)
    if carnet and "cliente_carnet" in vcols:
        customer_filters.append("cliente_carnet=%s")
        params.append(carnet)
    if not customer_filters:
        return "0.00"
    cursor.execute(
        f"SELECT COALESCE(SUM(total),0) AS total FROM ventas WHERE cliente_id=%s AND ({' OR '.join(customer_filters)})",
        tuple(params),
    )
    return str((cursor.fetchone() or {}).get("total") or "0.00")


@sales_customer_bp.route("/api/clientes")
@login_required
def buscar_clientes():
    if not g.user or g.user.get("rol_codigo") not in {"ADMIN_TIENDA", "CAJERO", "VENDEDOR"}:
        return jsonify({"error": "Tu rol no tiene acceso a ventas."}), 403
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    with db_cursor() as cursor:
        if not table_exists(cursor, "clientes_finales"):
            return jsonify([])
        cols = table_columns(cursor, "clientes_finales")
        filters = []
        params = [g.user["cliente_id"]]
        if "carnet" in cols:
            filters.append("carnet LIKE %s")
            params.append(f"%{q}%")
        if "nit_ci" in cols:
            filters.append("nit_ci LIKE %s")
            params.append(f"%{q}%")
        if not filters:
            return jsonify([])
        cursor.execute(
            f"""
            SELECT id,
                   {name_expr(cols)} AS nombre,
                   {doc_expr(cols)} AS carnet,
                   {'celular' if 'celular' in cols else 'NULL'} AS celular,
                   {'ciudad' if 'ciudad' in cols else 'NULL'} AS ciudad,
                   {'detalle_envio' if 'detalle_envio' in cols else 'NULL'} AS detalle_envio
            FROM clientes_finales
            WHERE cliente_id=%s AND ({' OR '.join(filters)})
            ORDER BY updated_at DESC, id DESC
            LIMIT 8
            """,
            tuple(params),
        )
        rows = cursor.fetchall()
        for row in rows:
            row["total_compras"] = total_compras(cursor, g.user["cliente_id"], row.get("id"), row.get("carnet"))
        return jsonify(to_jsonable(rows))
