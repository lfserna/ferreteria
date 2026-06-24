from pathlib import Path

from app import create_app

app = create_app()


def ssl_context_from_config():
    ssl_mode = app.config.get("APP_SSL", "")
    if ssl_mode in {"1", "true", "yes", "adhoc"}:
        return "adhoc"
    if ssl_mode in {"cert", "file", "local"}:
        cert_path = Path(app.config.get("APP_SSL_CERT", "certs/dev-cert.pem"))
        key_path = Path(app.config.get("APP_SSL_KEY", "certs/dev-key.pem"))
        if not cert_path.exists() or not key_path.exists():
            raise RuntimeError(
                "No se encontraron los archivos SSL. Ejecuta: "
                "python scripts/generate_dev_cert.py TU_IP_LOCAL"
            )
        return str(cert_path), str(key_path)
    return None


if __name__ == "__main__":
    app.run(
        host=app.config.get("APP_HOST", "0.0.0.0"),
        port=app.config.get("APP_PORT", 5000),
        debug=app.config.get("FLASK_ENV") == "development",
        ssl_context=ssl_context_from_config(),
    )
