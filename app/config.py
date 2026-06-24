import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "cambiar-en-produccion")
    FLASK_ENV = os.getenv("FLASK_ENV", "production")

    DB_CONNECTION = os.getenv("DB_CONNECTION", "mysql")
    DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT = int(os.getenv("DB_PORT", "3306"))
    DB_NAME = os.getenv("DB_DATABASE", os.getenv("DB_NAME", "restaurante_sistema"))
    DB_USER = os.getenv("DB_USERNAME", os.getenv("DB_USER", "root"))
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")

    APP_TIMEZONE = os.getenv("APP_TIMEZONE", "America/La_Paz")
    APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
    APP_PORT = int(os.getenv("APP_PORT", "5000"))
    APP_SSL = os.getenv("APP_SSL", "").strip().lower()
    APP_SSL_CERT = os.getenv("APP_SSL_CERT", "cert.pem")
    APP_SSL_KEY = os.getenv("APP_SSL_KEY", "key.pem")

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
