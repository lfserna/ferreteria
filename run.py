from pathlib import Path

from app import create_app

app = create_app()


def ssl_context_from_config():
    ssl_enabled = app.config.get("APP_SSL", "") in {"1", "true", "yes", "on"}
    if not ssl_enabled:
        return None

    cert_path = Path(app.config.get("APP_SSL_CERT", "cert.pem"))
    key_path = Path(app.config.get("APP_SSL_KEY", "key.pem"))

    if not cert_path.exists() or not key_path.exists():
        raise RuntimeError(
            "HTTPS está activado pero faltan cert.pem/key.pem. "
            "Ejecuta: python scripts/generate_https_cert.py 192.168.10.13"
        )

    return str(cert_path), str(key_path)


if __name__ == "__main__":
    app.run(
        host=app.config.get("APP_HOST", "0.0.0.0"),
        port=app.config.get("APP_PORT", 5000),
        debug=app.config.get("FLASK_ENV") == "development",
        ssl_context=ssl_context_from_config(),
    )
