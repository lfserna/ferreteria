from app import create_app

app = create_app()


def ssl_context_from_config():
    ssl_mode = app.config.get("APP_SSL", "")
    if ssl_mode in {"1", "true", "yes", "adhoc"}:
        return "adhoc"
    return None


if __name__ == "__main__":
    app.run(
        host=app.config.get("APP_HOST", "0.0.0.0"),
        port=app.config.get("APP_PORT", 5000),
        debug=app.config.get("FLASK_ENV") == "development",
        ssl_context=ssl_context_from_config(),
    )
