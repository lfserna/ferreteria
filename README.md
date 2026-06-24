# Sistema de Ferretería

Aplicación Flask modular para una ferretería multi-cliente, multi-sucursal y multi-almacén.

## Funcionalidades iniciales

- Login por nombre de usuario y contraseña hasheada.
- Contexto visible de cliente, sucursal y almacén del usuario autenticado.
- Layout responsivo para laptop, tablet y celular.
- Barra inferior fija en móvil con acción principal sobresaliente para ventas.
- Buscador automático de productos por nombre, descripción, código interno o código de barras.
- Vista unificada de productos y stock por ubicación.
- Filtros automáticos sin botón de aplicar.
- Selección por burbujas para opciones cortas, como método de pago.
- Venta desde cero para cajero.
- Armado de carrito por vendedor y envío a caja.
- Pedidos enviados a caja visibles para cajeros.
- Validación de rebaja: el precio vendido no puede bajar del precio mínimo permitido.
- Confirmación de venta con llave de idempotencia para evitar duplicados por doble clic.
- Registro de auditoría para login, creación de usuario, cambios de límite, envío de orden a caja y confirmación de venta.
- Gestión de usuarios con username automático, por ejemplo Luis Serna → `lserna`.
- Contraseña por defecto para usuarios nuevos: `hola123`.
- Límite configurable de usuarios por cliente.
- Comprobante imprimible y PDF de 58 mm usando template editable.
- Base preparada para proveedores y compras opcionales.
- Preparado para ejecución local con `flask run` o `python run.py`, y producción con Gunicorn.

## Instalación

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edita `.env` con tus datos de MySQL.

## Base de datos

```bash
mysql -u root -p < database/schema.sql
mysql -u root -p restaurante_sistema < database/migrations/001_add_cliente_max_usuarios.sql
mysql -u root -p restaurante_sistema < database/seed_demo.sql
```

Si ya tenías la base creada antes de esta actualización, ejecuta solo:

```bash
mysql -u root -p restaurante_sistema < database/migrations/001_add_cliente_max_usuarios.sql
```

Usuario demo:

```text
Usuario: admin
Contraseña: Admin123!
```

Otros usuarios demo usan la misma contraseña: `cajero.centro`, `cajero.norte`, `vendedor.centro`, `vendedor.norte`, `almacen.central`.

Los usuarios creados desde el sistema se generan con contraseña por defecto `hola123`.

## Ejecución

```bash
flask run --host=0.0.0.0 --port=5000
python run.py
```

Con Gunicorn:

```bash
gunicorn -w 3 -b 0.0.0.0:5000 wsgi:app
```

## Comprobante

El diseño editable está en `app/templates/sales/receipt_58mm.html`.
