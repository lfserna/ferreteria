from contextlib import contextmanager

import mysql.connector
from flask import current_app
from mysql.connector import Error


def get_connection():
    if current_app.config.get("DB_CONNECTION") != "mysql":
        raise RuntimeError("Solo se configuró conexión MySQL para esta aplicación.")
    try:
        return mysql.connector.connect(
            host=current_app.config["DB_HOST"],
            port=current_app.config["DB_PORT"],
            user=current_app.config["DB_USER"],
            password=current_app.config["DB_PASSWORD"],
            database=current_app.config["DB_NAME"],
            autocommit=False,
        )
    except Error as error:
        raise RuntimeError(f"No se pudo conectar a MySQL: {error}") from error


@contextmanager
def db_cursor(dictionary=True, commit=False):
    connection = get_connection()
    cursor = connection.cursor(dictionary=dictionary)
    try:
        yield cursor
        if commit:
            connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


@contextmanager
def db_transaction(dictionary=True):
    connection = get_connection()
    cursor = connection.cursor(dictionary=dictionary)
    try:
        yield cursor, connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()
