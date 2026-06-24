import json
from flask import request, session


def dumps(value):
    if value is None:
        return None
    return json.dumps(value, default=str, ensure_ascii=False)


def log_audit(cursor, *, cliente_id, usuario_id=None, modulo, accion, tabla_afectada=None,
              registro_id=None, valor_anterior=None, valor_nuevo=None):
    usuario_id = usuario_id or session.get("user_id")
    cursor.execute(
        """
        INSERT INTO auditoria_eventos
            (cliente_id, usuario_id, modulo, accion, tabla_afectada, registro_id,
             valor_anterior_json, valor_nuevo_json, ip_address, user_agent, created_at)
        VALUES
            (%s, %s, %s, %s, %s, %s, CAST(%s AS JSON), CAST(%s AS JSON), %s, %s, NOW())
        """,
        (
            cliente_id,
            usuario_id,
            modulo,
            accion,
            tabla_afectada,
            registro_id,
            dumps(valor_anterior),
            dumps(valor_nuevo),
            request.headers.get("X-Forwarded-For", request.remote_addr),
            request.headers.get("User-Agent", "")[:255],
        ),
    )
