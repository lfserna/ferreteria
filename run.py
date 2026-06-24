from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(
        host=app.config.get("APP_HOST", "0.0.0.0"),
        port=app.config.get("APP_PORT", 5055),
        debug=app.config.get("FLASK_ENV") == "development",
    )
