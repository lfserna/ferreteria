from flask import Flask, g, session

from app.services.context_service import get_user_context


def create_app():
    app = Flask(__name__)
    app.config.from_object("app.config.Config")

    from app.routes.auth_routes import auth_bp
    from app.routes.dashboard_routes import dashboard_bp
    from app.routes.inventory_routes import inventory_bp
    from app.routes.product_routes import products_bp
    from app.routes.report_routes import reports_bp
    from app.routes.sales_routes import sales_bp
    from app.routes.user_routes import users_bp

    @app.before_request
    def load_logged_user():
        g.user = None
        user_id = session.get("user_id")
        if user_id:
            g.user = get_user_context(user_id)

    @app.context_processor
    def inject_layout_context():
        return {"current_user": g.get("user")}

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(products_bp)
    app.register_blueprint(inventory_bp)
    app.register_blueprint(sales_bp)
    app.register_blueprint(reports_bp)
    app.register_blueprint(users_bp)

    return app
