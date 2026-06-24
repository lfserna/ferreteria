from app.routes.inventory_product_create_fix import crear_producto_fixed
from app.routes.inventory_report_routes import inventario_reporte_pdf
from app.routes.inventory_v2_routes import inventory_bp


def _deferred_route_matches(deferred, route):
    for cell in getattr(deferred, "__closure__", None) or []:
        try:
            if cell.cell_contents == route:
                return True
        except ValueError:
            continue
    return False


inventory_bp.deferred_functions = [
    deferred
    for deferred in inventory_bp.deferred_functions
    if not _deferred_route_matches(deferred, "/productos/crear")
]

inventory_bp.add_url_rule(
    "/productos/crear",
    endpoint="crear_producto",
    view_func=crear_producto_fixed,
    methods=["POST"],
)

inventory_bp.add_url_rule(
    "/reportes/pdf",
    endpoint="inventario_reporte_pdf",
    view_func=inventario_reporte_pdf,
    methods=["GET"],
)
